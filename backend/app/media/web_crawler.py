"""
Medical web content crawler and extractor.

Crawls medical literature sites (PubMed, NEJM, WHO guidelines, etc.) to
extract articles, abstracts, references, and guideline content.  Supports
JavaScript-rendered pages via Playwright, with rate limiting and caching.

Typical use-cases:
    - Retrieving PubMed abstracts for cross-referencing OCR results
    - Crawling clinical practice guidelines
    - Extracting references from medical publications
    - Building knowledge bases from medical literature
"""

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    logger.info("playwright not installed – JavaScript rendering disabled")

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    logger.warning("requests not installed – HTTP crawling disabled")

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    logger.warning("beautifulsoup4 not installed – HTML parsing limited")


# ---------------------------------------------------------------------------
# Pydantic data models
# ---------------------------------------------------------------------------


class CrawledPage(BaseModel):
    """A single crawled web page with extracted content."""

    url: str = Field(..., description="Canonical URL of the page")
    title: str = Field(default="", description="Page title")
    content: str = Field(default="", description="Main text content (HTML stripped)")
    html: Optional[str] = Field(default=None, description="Raw HTML (optional, for debugging)")
    meta_description: str = Field(default="")
    meta_keywords: List[str] = Field(default_factory=list)
    language: str = Field(default="en", description="Detected language")
    status_code: int = Field(default=200)
    crawled_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(),
    )
    links: List[str] = Field(default_factory=list, description="Outbound links found on page")


class Article(BaseModel):
    """A medical article / paper extracted from PubMed or similar."""

    pmid: Optional[str] = Field(default=None, description="PubMed ID")
    pmc_id: Optional[str] = Field(default=None, description="PubMed Central ID")
    doi: Optional[str] = Field(default=None, description="Digital Object Identifier")
    title: str = Field(default="")
    authors: List[str] = Field(default_factory=list)
    journal: str = Field(default="")
    publication_date: Optional[str] = Field(default=None)
    abstract: str = Field(default="")
    keywords: List[str] = Field(default_factory=list)
    mesh_terms: List[str] = Field(default_factory=list, description="MeSH descriptors")
    url: str = Field(default="")
    source: str = Field(default="", description="Source database (pubmed, manual, etc.)")


class Reference(BaseModel):
    """A single bibliographic reference."""

    index: int = Field(..., description="Reference number / index")
    text: str = Field(..., description="Raw reference text")
    title: Optional[str] = Field(default=None, description="Extracted paper title")
    authors: List[str] = Field(default_factory=list)
    journal: Optional[str] = Field(default=None)
    year: Optional[int] = Field(default=None)
    doi: Optional[str] = Field(default=None)
    pmid: Optional[str] = Field(default=None)
    url: Optional[str] = Field(default=None)


class GuidelineContent(BaseModel):
    """Extracted clinical practice guideline."""

    title: str = Field(default="")
    source: str = Field(default="", description="Issuing organisation")
    url: str = Field(default="")
    publication_date: Optional[str] = Field(default=None)
    sections: List[Dict[str, str]] = Field(
        default_factory=list,
        description="List of {heading, content} dicts",
    )
    recommendations: List[str] = Field(
        default_factory=list,
        description="Extracted recommendation statements",
    )
    key_points: List[str] = Field(default_factory=list)
    target_population: str = Field(default="")
    raw_content: str = Field(default="")


class CrawledContent(BaseModel):
    """Result of crawling one or more URLs."""

    pages: List[CrawledPage] = Field(default_factory=list)
    articles: List[Article] = Field(default_factory=list)
    references: List[Reference] = Field(default_factory=list)
    guidelines: List[GuidelineContent] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    total_urls_crawled: int = Field(default=0)
    processing_time: float = Field(default=0.0)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Domains that require rate-limiting respect
_MEDICAL_DOMAINS = {
    "pubmed.ncbi.nlm.nih.gov",
    "www.ncbi.nlm.nih.gov",
    "www.who.int",
    "nejm.org",
    "www.nejm.org",
    "thelancet.com",
    "www.thelancet.com",
    "jama.jamanetwork.com",
    "bmj.com",
    "www.bmj.com",
    "www.cochranelibrary.com",
    "clinicalguidelines.nlm.nih.gov",
}

# PubMed E-utilities base URL
_PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# Respectful inter-request delay (seconds)
_DEFAULT_DELAY = 1.0
_MEDICAL_SITE_DELAY = 2.0

# Maximum crawl depth
_MAX_DEPTH = 2

# Cache directory
_CACHE_DIR_NAME = "web_cache"


# ---------------------------------------------------------------------------
# MedicalWebCrawler
# ---------------------------------------------------------------------------


class MedicalWebCrawler:
    """
    Crawl and extract content from medical websites and literature databases.

    Features:
        - PubMed search and article retrieval via NCBI E-utilities
        - General web crawling with depth control
        - JavaScript-rendered page support via Playwright
        - Reference extraction from academic pages
        - Guideline content extraction
        - Local file-system caching
        - Rate limiting with configurable delays
    """

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        request_timeout: int = 30,
        max_retries: int = 3,
        user_agent: Optional[str] = None,
    ) -> None:
        """
        Args:
            cache_dir: Directory for HTTP response caching.
            request_timeout: HTTP request timeout in seconds.
            max_retries: Number of retry attempts for failed requests.
            user_agent: Custom User-Agent header string.
        """
        self.cache_dir = Path(
            cache_dir or os.path.join(settings.UPLOAD_DIR, _CACHE_DIR_NAME)
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.user_agent = user_agent or (
            "MedicalHandwritingOCR/1.0 (Educational/Research; +https://github.com/example)"
        )

        self._session = self._build_session() if HAS_REQUESTS else None
        self._last_request_time: float = 0.0
        self._visited: Set[str] = set()

        logger.info(
            "MedicalWebCrawler initialised  cache=%s  timeout=%ds",
            self.cache_dir,
            request_timeout,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def crawl_url(
        self,
        url: str,
        max_depth: int = _MAX_DEPTH,
    ) -> CrawledContent:
        """
        Crawl a URL and extract all available content.

        Follows links up to *max_depth* levels deep, respecting rate limits
        and staying within the same domain.

        Args:
            url: Starting URL to crawl.
            max_depth: Maximum link-following depth.

        Returns:
            A :class:`CrawledContent` with pages, articles, and references.
        """
        t0 = time.time()
        result = CrawledContent()
        self._visited.clear()

        parsed = urlparse(url)
        base_domain = parsed.netloc

        # BFS crawl queue: (url, depth)
        queue: List[tuple] = [(url, 0)]

        while queue:
            current_url, depth = queue.pop(0)
            if depth > max_depth:
                continue
            if current_url in self._visited:
                continue

            normalized = self._normalize_url(current_url)
            if normalized in self._visited:
                continue
            self._visited.add(normalized)

            # Rate limiting
            self._rate_limit(current_url)

            try:
                page = self._fetch_page(current_url)
                if page is None:
                    result.errors.append(f"Failed to fetch: {current_url}")
                    continue

                result.pages.append(page)
                result.total_urls_crawled += 1

                # Try to extract references from the page
                refs = self.extract_references(current_url, page.content)
                result.references.extend(refs)

                # Queue same-domain links for further crawling
                if depth < max_depth:
                    for link in page.links:
                        link_parsed = urlparse(link)
                        if link_parsed.netloc == base_domain:
                            queue.append((link, depth + 1))

            except Exception as exc:
                logger.warning("Error crawling %s: %s", current_url, exc)
                result.errors.append(f"{current_url}: {str(exc)}")

        result.processing_time = round(time.time() - t0, 2)
        logger.info(
            "Crawl complete: %d pages, %d refs, %d errors in %.2fs",
            len(result.pages),
            len(result.references),
            len(result.errors),
            result.processing_time,
        )
        return result

    def search_pubmed(
        self,
        query: str,
        max_results: int = 20,
        retstart: int = 0,
    ) -> List[Article]:
        """
        Search PubMed for medical articles matching the query.

        Uses the NCBI E-utilities REST API (no API key required for modest use).

        Args:
            query: PubMed search string (supports Boolean operators, MeSH, etc.).
            max_results: Maximum number of articles to return.
            retstart: Starting index for pagination.

        Returns:
            List of :class:`Article` objects with metadata and abstracts.
        """
        if not HAS_REQUESTS:
            logger.error("requests library required for PubMed search")
            return []

        logger.info("PubMed search: '%s'  max_results=%d", query, max_results)
        self._rate_limit("pubmed.ncbi.nlm.nih.gov")

        try:
            # Step 1: Search for PMIDs
            search_params = {
                "db": "pubmed",
                "term": query,
                "retmax": max_results,
                "retstart": retstart,
                "retmode": "json",
                "sort": "relevance",
            }
            cache_key = self._cache_key("pubmed_search", search_params)
            cached = self._read_cache(cache_key)
            if cached:
                search_data = cached
            else:
                resp = self._session.get(
                    _PUBMED_ESEARCH,
                    params=search_params,
                    timeout=self.request_timeout,
                )
                resp.raise_for_status()
                search_data = resp.json()
                self._write_cache(cache_key, search_data)

            pmids = search_data.get("esearchresult", {}).get("idlist", [])
            if not pmids:
                logger.info("No PubMed results for: %s", query)
                return []

            # Step 2: Fetch article details
            self._rate_limit("pubmed.ncbi.nlm.nih.gov")
            fetch_params = {
                "db": "pubmed",
                "id": ",".join(pmids),
                "retmode": "xml",
            }
            cache_key2 = self._cache_key("pubmed_fetch", fetch_params)
            cached2 = self._read_cache(cache_key2)
            if cached2:
                fetch_xml = cached2
            else:
                resp2 = self._session.get(
                    _PUBMED_EFETCH,
                    params=fetch_params,
                    timeout=self.request_timeout,
                )
                resp2.raise_for_status()
                fetch_xml = resp2.text
                self._write_cache(cache_key2, fetch_xml)

            articles = self._parse_pubmed_xml(fetch_xml, query)
            logger.info("PubMed: %d articles retrieved", len(articles))
            return articles

        except Exception as exc:
            logger.error("PubMed search failed: %s", exc)
            return []

    def crawl_guidelines(self, source_url: str) -> GuidelineContent:
        """
        Crawl and extract clinical practice guideline content.

        Attempts to extract structured sections, recommendations, and
        key points from guideline pages.

        Args:
            source_url: URL of the guideline page.

        Returns:
            A :class:`GuidelineContent` with structured guideline data.
        """
        self._rate_limit(source_url)
        logger.info("Crawling guideline: %s", source_url)

        page = self._fetch_page(source_url)
        if page is None:
            return GuidelineContent(url=source_url)

        # Try to extract structured sections
        sections = self._extract_sections(page.content)
        recommendations = self._extract_recommendations(page.content)
        key_points = self._extract_key_points(page.content)

        # Detect source organisation
        source = self._detect_source_organisation(source_url)

        return GuidelineContent(
            title=page.title,
            source=source,
            url=source_url,
            sections=sections,
            recommendations=recommendations,
            key_points=key_points,
            raw_content=page.content,
            crawled_at=page.crawled_at,
        )

    def extract_references(
        self,
        url: str,
        content: Optional[str] = None,
    ) -> List[Reference]:
        """
        Extract bibliographic references from a web page.

        Parses reference sections from PubMed, journal articles, and
        guideline pages.

        Args:
            url: URL of the page containing references.
            content: Pre-extracted text content. If ``None``, the page
                is fetched first.

        Returns:
            List of :class:`Reference` objects.
        """
        if content is None:
            page = self._fetch_page(url)
            if page is None:
                return []
            content = page.content

        references: List[Reference] = []

        if not HAS_BS4:
            # Simple regex-based extraction as fallback
            return self._extract_references_regex(content)

        # BeautifulSoup-based extraction
        # Look for reference sections in common patterns
        try:
            soup = BeautifulSoup(content, "html.parser")

            # Pattern 1: PubMed-style references
            ref_list = soup.find("div", class_=re.compile(r"references|ref-list", re.I))
            if ref_list is None:
                ref_list = soup.find("ol", class_=re.compile(r"references|ref-list", re.I))
            if ref_list is None:
                ref_list = soup.find("section", id=re.compile(r"references|refs", re.I))

            if ref_list:
                items = ref_list.find_all(["li", "p", "div"], recursive=False)
                for idx, item in enumerate(items):
                    text = item.get_text(strip=True)
                    if text:
                        references.append(
                            self._parse_single_reference(idx, text)
                        )
            else:
                # Pattern 2: Numbered references in text
                references = self._extract_references_regex(content)

        except Exception as exc:
            logger.debug("Reference extraction error: %s", exc)
            references = self._extract_references_regex(content)

        return references

    # ------------------------------------------------------------------
    # Page fetching
    # ------------------------------------------------------------------

    def _fetch_page(self, url: str) -> Optional[CrawledPage]:
        """
        Fetch a single web page.

        Uses Playwright for JavaScript-heavy sites (medical databases),
        falling back to plain HTTP requests otherwise.

        Returns:
            A :class:`CrawledPage`, or ``None`` on failure.
        """
        # Check cache first
        cache_key = self._cache_key("page", {"url": url})
        cached = self._read_cache(cache_key)
        if cached:
            return CrawledPage(**cached)

        page = None

        # Try Playwright for JS-rendered pages
        if HAS_PLAYWRIGHT and self._needs_playwright(url):
            page = self._fetch_with_playwright(url)

        # Fall back to requests
        if page is None and HAS_REQUESTS:
            page = self._fetch_with_requests(url)

        if page is not None:
            self._write_cache(cache_key, page.model_dump())

        return page

    def _fetch_with_playwright(self, url: str) -> Optional[CrawledPage]:
        """Fetch a page using headless Playwright for JavaScript rendering."""
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=self.user_agent,
                    viewport={"width": 1280, "height": 720},
                )
                page = context.new_page()

                page.goto(url, wait_until="networkidle", timeout=self.request_timeout * 1000)

                title = page.title()
                content = page.inner_text("body") or ""
                links = [
                    href
                    for href in page.eval_on_selector_all(
                        "a[href]",
                        "els => els.map(e => e.href)",
                    )
                    if href.startswith("http")
                ]

                browser.close()

                return CrawledPage(
                    url=url,
                    title=title,
                    content=content,
                    language=self._detect_language(content),
                    links=links,
                )
        except PlaywrightTimeout:
            logger.warning("Playwright timeout for %s", url)
            return None
        except Exception as exc:
            logger.warning("Playwright error for %s: %s", url, exc)
            return None

    def _fetch_with_requests(self, url: str) -> Optional[CrawledPage]:
        """Fetch a page using plain HTTP requests."""
        if self._session is None:
            return None

        try:
            resp = self._session.get(url, timeout=self.request_timeout)
            resp.raise_for_status()

            # Parse HTML
            title = ""
            content = ""
            links: List[str] = []
            meta_desc = ""
            meta_kw: List[str] = []

            if HAS_BS4:
                soup = BeautifulSoup(resp.text, "html.parser")

                # Title
                title_tag = soup.find("title")
                if title_tag:
                    title = title_tag.get_text(strip=True)

                # Meta
                desc_tag = soup.find("meta", attrs={"name": "description"})
                if desc_tag:
                    meta_desc = desc_tag.get("content", "")
                kw_tag = soup.find("meta", attrs={"name": "keywords"})
                if kw_tag:
                    meta_kw = [k.strip() for k in kw_tag.get("content", "").split(",")]

                # Main content – try common containers
                main_content = (
                    soup.find("article")
                    or soup.find("main")
                    or soup.find("div", class_=re.compile(r"content|article|body", re.I))
                    or soup.find("body")
                )
                if main_content:
                    # Remove script/style
                    for tag in main_content.find_all(["script", "style", "nav", "footer"]):
                        tag.decompose()
                    content = main_content.get_text(separator="\n", strip=True)

                # Links
                for a_tag in soup.find_all("a", href=True):
                    href = urljoin(url, a_tag["href"])
                    if href.startswith("http"):
                        links.append(href)
            else:
                # Minimal fallback without BeautifulSoup
                content = resp.text

            return CrawledPage(
                url=url,
                title=title,
                content=content,
                meta_description=meta_desc,
                meta_keywords=meta_kw,
                language=self._detect_language(content),
                status_code=resp.status_code,
                links=list(set(links)),
            )
        except Exception as exc:
            logger.warning("HTTP fetch error for %s: %s", url, exc)
            return None

    # ------------------------------------------------------------------
    # PubMed XML parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_pubmed_xml(xml_text: str, query: str = "") -> List[Article]:
        """Parse PubMed eFetch XML response into Article objects."""
        articles: List[Article] = []

        if not HAS_BS4:
            logger.warning("beautifulsoup4 required for PubMed XML parsing")
            return articles

        try:
            soup = BeautifulSoup(xml_text, "xml")
            for article_tag in soup.find_all("PubmedArticle"):
                pmid_tag = article_tag.find("PMID")
                pmid = pmid_tag.get_text(strip=True) if pmid_tag else None

                # Title
                title_tag = article_tag.find("ArticleTitle")
                title = ""
                if title_tag:
                    title = title_tag.get_text(strip=True)

                # Authors
                authors: List[str] = []
                author_list = article_tag.find("AuthorList")
                if author_list:
                    for author in author_list.find_all("Author"):
                        ln = author.find("LastName")
                        fn = author.find("ForeName")
                        if ln:
                            name = ln.get_text(strip=True)
                            if fn:
                                name += f" {fn.get_text(strip=True)}"
                            authors.append(name)

                # Journal
                journal = ""
                journal_tag = article_tag.find("Journal")
                if journal_tag:
                    jt = journal_tag.find("Title")
                    if jt:
                        journal = jt.get_text(strip=True)

                # Abstract
                abstract = ""
                abstract_tag = article_tag.find("Abstract")
                if abstract_tag:
                    abs_texts = abstract_tag.find_all("AbstractText")
                    abstract = " ".join(
                        t.get_text(strip=True) for t in abs_texts
                    )

                # Keywords / MeSH
                mesh_terms: List[str] = []
                mesh_list = article_tag.find("MeshHeadingList")
                if mesh_list:
                    for mesh in mesh_list.find_all("MeshHeading"):
                        desc = mesh.find("DescriptorName")
                        if desc:
                            mesh_terms.append(desc.get_text(strip=True))

                keywords: List[str] = []
                kw_list = article_tag.find("KeywordList")
                if kw_list:
                    for kw in kw_list.find_all("Keyword"):
                        keywords.append(kw.get_text(strip=True))

                # DOI
                doi = None
                for eid in article_tag.find_all("ArticleId"):
                    if eid.get("IdType") == "doi":
                        doi = eid.get_text(strip=True)
                        break

                # PMC ID
                pmc_id = None
                for eid in article_tag.find_all("ArticleId"):
                    if eid.get("IdType") == "pmc":
                        pmc_id = eid.get_text(strip=True)
                        break

                # Publication date
                pub_date = None
                pub_date_tag = article_tag.find("PubDate")
                if pub_date_tag:
                    y = pub_date_tag.find("Year")
                    m = pub_date_tag.find("Month")
                    d = pub_date_tag.find("Day")
                    parts = []
                    if y:
                        parts.append(y.get_text(strip=True))
                    if m:
                        parts.append(m.get_text(strip=True))
                    if d:
                        parts.append(d.get_text(strip=True))
                    pub_date = " ".join(parts) if parts else None

                articles.append(
                    Article(
                        pmid=pmid,
                        pmc_id=pmc_id,
                        doi=doi,
                        title=title,
                        authors=authors,
                        journal=journal,
                        publication_date=pub_date,
                        abstract=abstract,
                        keywords=keywords,
                        mesh_terms=mesh_terms,
                        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                        source="pubmed",
                    )
                )
        except Exception as exc:
            logger.error("PubMed XML parse error: %s", exc)

        return articles

    # ------------------------------------------------------------------
    # Guideline extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_sections(content: str) -> List[Dict[str, str]]:
        """Extract numbered/heading sections from guideline text."""
        sections: List[Dict[str, str]] = []

        # Match heading-like lines (e.g. "1. Introduction", "## Methods")
        pattern = re.compile(
            r"^(?:#{1,4}\s+|\d+[\.\)]\s+)(.+?)$",
            re.MULTILINE,
        )
        matches = list(pattern.finditer(content))

        for i, match in enumerate(matches):
            heading = match.group(1).strip()
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            body = content[start:end].strip()

            if body:
                sections.append({"heading": heading, "content": body})

        return sections

    @staticmethod
    def _extract_recommendations(content: str) -> List[str]:
        """Extract recommendation statements from guideline text."""
        recommendations: List[str] = []

        # Common patterns for recommendation statements
        patterns = [
            re.compile(r"(?:We recommend|Recommendation\s*\d*[:\.]\s*)(.+?)(?:\n\n|\n(?=\d)|$)", re.I | re.S),
            re.compile(r"(?:نوصي بـ|التوصية\s*\d*[:\.]\s*)(.+?)(?:\n\n|\n(?=\d)|$)", re.S),
        ]

        for pat in patterns:
            for m in pat.finditer(content):
                text = m.group(1).strip()
                if len(text) > 20:  # Filter out short fragments
                    recommendations.append(text)

        return recommendations

    @staticmethod
    def _extract_key_points(content: str) -> List[str]:
        """Extract key points / summary statements."""
        key_points: List[str] = []

        # Match bullet-pointed items
        bullet_pattern = re.compile(
            r"^\s*[-•▪▸]\s+(.+?)$",
            re.MULTILINE,
        )
        for m in bullet_pattern.finditer(content):
            text = m.group(1).strip()
            if len(text) > 15:
                key_points.append(text)

        return key_points[:50]  # Cap at 50

    @staticmethod
    def _detect_source_organisation(url: str) -> str:
        """Detect the issuing organisation from a URL."""
        domain_map = {
            "who.int": "World Health Organization (WHO)",
            "nejm.org": "New England Journal of Medicine (NEJM)",
            "thelancet.com": "The Lancet",
            "jama.jamanetwork.com": "JAMA",
            "bmj.com": "British Medical Journal (BMJ)",
            "cochranelibrary.com": "Cochrane Library",
            "nih.gov": "National Institutes of Health (NIH)",
            "cdc.gov": "Centers for Disease Control (CDC)",
            "clinicalguidelines.nlm.nih.gov": "NIH Clinical Guidelines",
        }
        parsed = urlparse(url)
        for domain, org in domain_map.items():
            if domain in parsed.netloc:
                return org
        return parsed.netloc

    # ------------------------------------------------------------------
    # Reference extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_references_regex(content: str) -> List[Reference]:
        """Fallback regex-based reference extraction."""
        references: List[Reference] = []

        # Match numbered reference patterns
        patterns = [
            re.compile(r"^\s*(\d+)\.\s+(.+?)$", re.MULTILINE),
            re.compile(r"\[(\d+)\]\s+(.+?)(?:\n\n|\n(?=\[\d+\]))", re.S),
        ]

        for pat in patterns:
            for m in pat.finditer(content):
                idx = int(m.group(1))
                text = m.group(2).strip()
                if len(text) > 20:
                    year_match = re.search(r"\b(19|20)\d{2}\b", text)
                    year = int(year_match.group()) if year_match else None

                    references.append(
                        Reference(
                            index=idx,
                            text=text,
                            year=year,
                        )
                    )

        # Deduplicate by index
        seen: set = set()
        deduped: List[Reference] = []
        for ref in references:
            if ref.index not in seen:
                seen.add(ref.index)
                deduped.append(ref)

        return sorted(deduped, key=lambda r: r.index)

    @staticmethod
    def _parse_single_reference(index: int, text: str) -> Reference:
        """Parse a single reference string into structured data."""
        year_match = re.search(r"\b(19|20)\d{2}\b", text)
        year = int(year_match.group()) if year_match else None

        # Try to extract DOI
        doi_match = re.search(r"10\.\d{4,}/[^\s]+", text)
        doi = doi_match.group().rstrip(".") if doi_match else None

        return Reference(
            index=index,
            text=text,
            year=year,
            doi=doi,
        )

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _needs_playwright(self, url: str) -> bool:
        """Determine if a URL likely needs JavaScript rendering."""
        parsed = urlparse(url)
        js_domains = {
            "pubmed.ncbi.nlm.nih.gov",
            "www.ncbi.nlm.nih.gov",
            "clinicalguidelines.nlm.nih.gov",
        }
        return parsed.netloc in js_domains

    def _rate_limit(self, url: str) -> None:
        """Apply rate limiting before making a request."""
        now = time.time()
        parsed = urlparse(url)

        # Medical / academic sites get longer delays
        delay = _MEDICAL_SITE_DELAY if parsed.netloc in _MEDICAL_DOMAINS else _DEFAULT_DELAY

        elapsed = now - self._last_request_time
        if elapsed < delay:
            sleep_time = delay - elapsed
            time.sleep(sleep_time)

        self._last_request_time = time.time()

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalize a URL for deduplication."""
        parsed = urlparse(url)
        # Remove fragment, sort params
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    @staticmethod
    def _detect_language(text: str) -> str:
        """Detect language from text content (Arabic vs English heuristic)."""
        if not text:
            return "en"
        arabic_chars = sum(1 for c in text if "\u0600" <= c <= "\u06FF")
        ratio = arabic_chars / max(len(text), 1)
        return "ar" if ratio > 0.1 else "en"

    def _build_session(self) -> "requests.Session":
        """Build an HTTP session with retry logic."""
        session = requests.Session()
        session.headers.update({"User-Agent": self.user_agent})

        retry = Retry(
            total=self.max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        return session

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------

    def _cache_key(self, prefix: str, params: dict) -> str:
        """Generate a filesystem-safe cache key."""
        raw = f"{prefix}:{json.dumps(params, sort_keys=True)}"
        h = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return f"{prefix}_{h}.json"

    def _read_cache(self, key: str) -> Optional[dict]:
        """Read cached data if it exists and is fresh (< 24h)."""
        cache_path = self.cache_dir / key
        if not cache_path.exists():
            return None
        try:
            stat = cache_path.stat()
            age_hours = (time.time() - stat.st_mtime) / 3600
            if age_hours > 24:
                return None
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _write_cache(self, key: str, data) -> None:
        """Write data to cache."""
        cache_path = self.cache_dir / key
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, default=str)
        except Exception as exc:
            logger.debug("Cache write failed for %s: %s", key, exc)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_web_crawler: Optional[MedicalWebCrawler] = None


def get_web_crawler(
    cache_dir: Optional[str] = None,
    request_timeout: int = 30,
) -> MedicalWebCrawler:
    """Get or create the shared :class:`MedicalWebCrawler` singleton."""
    global _web_crawler
    if _web_crawler is None:
        _web_crawler = MedicalWebCrawler(
            cache_dir=cache_dir,
            request_timeout=request_timeout,
        )
    return _web_crawler
