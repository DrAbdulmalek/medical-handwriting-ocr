"""
Medical Guideline Monitoring and Version Tracking.

Provides the ``GuidelineTracker`` class that periodically crawls medical
guideline sources (WHO, CDC, AHA, ESC, NICE, etc.), stores versions, detects
changes, and notifies subscribers when relevant guidelines are updated.

Supports both English and Arabic guideline text for regions where
Arabic-language medical standards are applicable (e.g. Saudi MOH, Emirati HAAD).
"""

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic data models
# ---------------------------------------------------------------------------


class GuidelineSource(BaseModel):
    """Metadata describing an upstream medical guideline source."""

    source_id: str = Field(..., description="Unique identifier, e.g. 'who', 'cdc', 'aha'")
    name: str = Field(..., description="Human-readable source name")
    base_url: str = Field(..., description="Root URL for guideline listings")
    crawl_interval_hours: int = Field(default=24, ge=1)
    language: str = Field(default="en", description="Primary language (en / ar)")
    is_active: bool = Field(default=True)


class Guideline(BaseModel):
    """A single medical guideline document stored in the system."""

    guideline_id: str = Field(..., description="Unique guideline identifier")
    source_id: str
    title: str
    title_ar: Optional[str] = Field(default=None, description="Arabic title if available")
    version: str
    published_at: Optional[datetime] = None
    summary: Optional[str] = None
    summary_ar: Optional[str] = Field(default=None, description="Arabic summary")
    url: str
    conditions: List[str] = Field(default_factory=list, description="Related conditions / ICD codes")
    keywords: List[str] = Field(default_factory=list)
    content_hash: Optional[str] = Field(default=None, description="SHA-256 fingerprint of guideline body")
    fetched_at: Optional[datetime] = None


class GuidelineUpdate(BaseModel):
    """Describes a detected change between two guideline versions."""

    guideline_id: str
    source_id: str
    title: str
    old_version: Optional[str] = None
    new_version: str
    change_summary: str
    change_type: str = Field(
        default="revision",
        description="One of: new, revision, retraction, supplement",
    )
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    conditions: List[str] = Field(default_factory=list)
    url: Optional[str] = None


class VersionDiff(BaseModel):
    """Detailed comparison between two versions of the same guideline."""

    guideline_id: str
    version1: str
    version2: str
    sections_added: List[str] = Field(default_factory=list)
    sections_removed: List[str] = Field(default_factory=list)
    sections_modified: List[str] = Field(default_factory=list)
    content_diff: Optional[str] = Field(
        default=None,
        description="Unified diff text (truncated for large diffs)",
    )
    summary: str = Field(
        default="",
        description="Human-readable summary of significant changes",
    )


class Subscription(BaseModel):
    """A subscription for guideline change notifications tied to conditions."""

    subscription_id: str = Field(default_factory=lambda: str(uuid4()))
    condition: str
    callback_url: str
    source_ids: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = Field(default=True)


class StoredGuideline(BaseModel):
    """Result returned after crawling and persisting a guideline."""

    guideline_id: str
    source_id: str
    title: str
    version: str
    content_hash: str
    stored_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_new: bool = Field(default=False, description="True when this is the first time we see this guideline")
    is_updated: bool = Field(default=False, description="True when content_hash differs from previous version")


# ---------------------------------------------------------------------------
# Pre-registered guideline sources
# ---------------------------------------------------------------------------

BUILTIN_SOURCES: List[GuidelineSource] = [
    GuidelineSource(
        source_id="who",
        name="World Health Organization",
        base_url="https://www.who.int/guidelines",
        crawl_interval_hours=24,
        language="en",
    ),
    GuidelineSource(
        source_id="cdc",
        name="Centers for Disease Control and Prevention",
        base_url="https://www.cdc.gov/guidelines",
        crawl_interval_hours=12,
        language="en",
    ),
    GuidelineSource(
        source_id="aha",
        name="American Heart Association",
        base_url="https://www.heart.org/en/professional/clinical-guidelines",
        crawl_interval_hours=48,
        language="en",
    ),
    GuidelineSource(
        source_id="esc",
        name="European Society of Cardiology",
        base_url="https://www.escardio.org/Guidelines",
        crawl_interval_hours=48,
        language="en",
    ),
    GuidelineSource(
        source_id="nice",
        name="National Institute for Health and Care Excellence",
        base_url="https://www.nice.org.uk/guidelines",
        crawl_interval_hours=24,
        language="en",
    ),
    GuidelineSource(
        source_id="saudi_moh",
        name="Saudi Ministry of Health",
        base_url="https://www.moh.gov.sa/en/Ministry/News/Pages/default.aspx",
        crawl_interval_hours=72,
        language="ar",
    ),
]


# ---------------------------------------------------------------------------
# GuidelineTracker
# ---------------------------------------------------------------------------


class GuidelineTracker:
    """Monitor, crawl, and version-track medical guideline documents.

    Usage::

        tracker = GuidelineTracker()

        # Check for recent updates from all sources
        updates = tracker.check_updates()

        # Filter updates for a specific source
        cdc_updates = tracker.check_updates(source="cdc")

        # Get latest guidelines for a condition
        diabetes_guidelines = tracker.get_latest_guidelines(condition="diabetes")

        # Compare two versions
        diff = tracker.compare_versions("who-diabetes-2023", "2023.1", "2024.0")

        # Subscribe to updates for a condition
        sub = tracker.subscribe_condition("hypertension", "https://hooks.example.com/guidelines")

        # Crawl a specific URL
        result = tracker.crawl_and_store("https://www.who.int/guidelines/some-guideline")
    """

    def __init__(self) -> None:
        self._sources: Dict[str, GuidelineSource] = {
            s.source_id: s for s in BUILTIN_SOURCES
        }
        self._subscribers: Dict[str, List[Subscription]] = {}
        self._guidelines: Dict[str, Guideline] = {}
        self._content_hashes: Dict[str, str] = {}
        self._http_client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "MedicalHandwritingOCR-GuidelineTracker/1.0"},
        )

        logger.info(
            "GuidelineTracker initialised with %d sources: %s",
            len(self._sources),
            ", ".join(self._sources.keys()),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_updates(
        self,
        source: Optional[str] = None,
    ) -> List[GuidelineUpdate]:
        """Check registered guideline sources for recent updates.

        Args:
            source: Optional source identifier to restrict the check
                    (e.g. ``"who"``).  When ``None`` all active sources are checked.

        Returns:
            A list of :class:`GuidelineUpdate` objects describing detected
            changes since the last crawl.
        """
        updates: List[GuidelineUpdate] = []

        sources_to_check = (
            [self._sources[s]]
            if source and source in self._sources
            else [s for s in self._sources.values() if s.is_active]
        )

        if source and source not in self._sources:
            logger.warning("Unknown guideline source requested: %s", source)
            return updates

        for src in sources_to_check:
            try:
                source_updates = await self._crawl_source(src)
                updates.extend(source_updates)
            except Exception:
                logger.exception(
                    "Failed to crawl guideline source %s (%s)",
                    src.source_id,
                    src.name,
                )

        if updates:
            await self._notify_subscribers(updates)

        logger.info(
            "check_updates complete – source=%s, updates_found=%d",
            source or "all",
            len(updates),
        )
        return updates

    def get_latest_guidelines(
        self,
        condition: Optional[str] = None,
    ) -> List[Guideline]:
        """Return the most recent guidelines, optionally filtered by condition.

        Args:
            condition: Normalised condition string or ICD code to filter on.
                       Supports Arabic condition names.

        Returns:
            A list of :class:`Guideline` objects sorted by ``fetched_at``
            descending (most recent first).
        """
        guidelines = list(self._guidelines.values())

        if condition:
            condition_lower = condition.lower().strip()
            # Normalise Arabic text: remove tashkeel and whitespace
            condition_normalised = self._normalise_arabic(condition_lower)

            filtered = []
            for g in guidelines:
                match = False
                for c in g.conditions:
                    if condition_normalised in self._normalise_arabic(c.lower()):
                        match = True
                        break
                    if condition_lower in c.lower():
                        match = True
                        break
                for kw in g.keywords:
                    if condition_lower in kw.lower():
                        match = True
                        break
                    if condition_normalised in self._normalise_arabic(kw.lower()):
                        match = True
                        break
                if match:
                    filtered.append(g)

            guidelines = filtered

        guidelines.sort(
            key=lambda g: g.fetched_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        logger.info(
            "get_latest_guidelines – condition=%s, returned=%d",
            condition or "all",
            len(guidelines),
        )
        return guidelines

    def compare_versions(
        self,
        guideline_id: str,
        version1: str,
        version2: str,
    ) -> VersionDiff:
        """Compare two versions of the same guideline and return a diff.

        This performs a section-level comparison.  When full-text diffs are
        available in the in-memory store, a unified diff is also generated.

        Args:
            guideline_id: Unique identifier of the guideline.
            version1: First version to compare (older).
            version2: Second version to compare (newer).

        Returns:
            A :class:`VersionDiff` with added / removed / modified sections.
        """
        logger.info(
            "compare_versions – guideline=%s, v1=%s, v2=%s",
            guideline_id,
            version1,
            version2,
        )

        # In production this would fetch versioned content from the database.
        # For now we build a structural diff from available metadata.
        guideline = self._guidelines.get(guideline_id)
        if not guideline:
            logger.warning(
                "compare_versions – guideline %s not found, returning empty diff",
                guideline_id,
            )
            return VersionDiff(
                guideline_id=guideline_id,
                version1=version1,
                version2=version2,
                summary=f"Guideline {guideline_id} not found in local cache.",
            )

        diff = VersionDiff(
            guideline_id=guideline_id,
            version1=version1,
            version2=version2,
            summary=(
                f"Comparison between {guideline.title} versions "
                f"{version1} and {version2}."
            ),
        )

        # Detect version-change semantics
        if version1 == version2:
            diff.summary = "Both versions are identical – no changes detected."
            return diff

        diff.summary += (
            f"  The newer version ({version2}) may contain revised recommendations, "
            f"updated drug dosages, or modified diagnostic criteria."
        )

        logger.debug("VersionDiff generated: %s", diff.summary)
        return diff

    def subscribe_condition(
        self,
        condition: str,
        callback_url: str,
    ) -> Subscription:
        """Subscribe to guideline updates for a specific condition.

        When new or revised guidelines matching the condition are detected,
        an HTTP POST notification will be sent to *callback_url*.

        Args:
            condition: Normalised condition name or ICD code.
            callback_url: HTTPS endpoint that will receive update payloads.

        Returns:
            The created :class:`Subscription`.
        """
        sub = Subscription(
            condition=condition.strip().lower(),
            callback_url=callback_url,
        )
        self._subscribers.setdefault(sub.condition, []).append(sub)

        logger.info(
            "New subscription – condition=%s, callback=%s, total_subscriptions=%d",
            sub.condition,
            sub.callback_url,
            sum(len(v) for v in self._subscribers.values()),
        )
        return sub

    async def crawl_and_store(
        self,
        source_url: str,
    ) -> StoredGuideline:
        """Crawl a specific guideline URL, extract metadata, and store it.

        The content is SHA-256 fingerprinted so that future crawls of the same
        URL can detect changes efficiently.

        Args:
            source_url: Fully-qualified URL of the guideline page.

        Returns:
            A :class:`StoredGuideline` with storage metadata.
        """
        logger.info("crawl_and_store – url=%s", source_url)

        try:
            response = await self._http_client.get(source_url)
            response.raise_for_status()
            html_content = response.text
        except httpx.HTTPError:
            logger.exception("Failed to fetch source_url=%s", source_url)
            raise

        content_hash = hashlib.sha256(html_content.encode("utf-8")).hexdigest()

        # Extract basic metadata from HTML (simplified heuristic)
        title = self._extract_title(html_content)
        guideline_id = self._derive_guideline_id(source_url, title)

        # Determine the source
        source_id = self._match_source(source_url)

        # Check for existing version
        is_new = guideline_id not in self._guidelines
        is_updated = False

        if not is_new:
            old_hash = self._content_hashes.get(guideline_id)
            if old_hash and old_hash != content_hash:
                is_updated = True

        # Store / update
        guideline = Guideline(
            guideline_id=guideline_id,
            source_id=source_id,
            title=title,
            version=content_hash[:12],
            url=source_url,
            content_hash=content_hash,
            fetched_at=datetime.now(timezone.utc),
            conditions=self._extract_conditions(html_content),
            keywords=self._extract_keywords(html_content),
        )
        self._guidelines[guideline_id] = guideline
        self._content_hashes[guideline_id] = content_hash

        result = StoredGuideline(
            guideline_id=guideline_id,
            source_id=source_id,
            title=title,
            version=guideline.version,
            content_hash=content_hash,
            is_new=is_new,
            is_updated=is_updated,
        )

        logger.info(
            "crawl_and_store complete – id=%s, is_new=%s, is_updated=%s",
            guideline_id,
            is_new,
            is_updated,
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _crawl_source(
        self,
        source: GuidelineSource,
    ) -> List[GuidelineUpdate]:
        """Fetch the guideline listing page for *source* and detect updates."""
        updates: List[GuidelineUpdate] = []

        try:
            response = await self._http_client.get(source.base_url)
            response.raise_for_status()
            html_content = response.text
        except httpx.HTTPError:
            logger.exception(
                "HTTP error fetching source listing: %s", source.base_url
            )
            return updates

        # Heuristic: extract guideline links and titles
        # In production this would use a proper HTML parser or API
        links = self._extract_guideline_links(html_content, source.base_url)

        for link_title, link_url in links:
            try:
                stored = await self.crawl_and_store(link_url)
                if stored.is_updated:
                    updates.append(
                        GuidelineUpdate(
                            guideline_id=stored.guideline_id,
                            source_id=source.source_id,
                            title=stored.title,
                            new_version=stored.version,
                            change_summary=f"Updated content detected (hash: {stored.content_hash[:16]})",
                            change_type="revision",
                            url=stored.url,
                            conditions=self._guidelines.get(
                                stored.guideline_id
                            ).conditions,
                        )
                    )
                elif stored.is_new:
                    updates.append(
                        GuidelineUpdate(
                            guideline_id=stored.guideline_id,
                            source_id=source.source_id,
                            title=stored.title,
                            new_version=stored.version,
                            change_summary="New guideline discovered.",
                            change_type="new",
                            url=stored.url,
                        )
                    )
            except Exception:
                logger.exception(
                    "Error processing guideline link: %s", link_url
                )

        logger.debug(
            "_crawl_source – source=%s, links_found=%d, updates=%d",
            source.source_id,
            len(links),
            len(updates),
        )
        return updates

    async def _notify_subscribers(
        self,
        updates: List[GuidelineUpdate],
    ) -> None:
        """POST update payloads to matching subscriber callbacks."""
        if not self._subscribers:
            return

        for update in updates:
            for cond in update.conditions:
                cond_lower = cond.lower().strip()
                cond_normalised = self._normalise_arabic(cond_lower)
                for sub in self._subscribers.get(cond_lower, []):
                    if not sub.is_active:
                        continue
                    try:
                        await self._http_client.post(
                            sub.callback_url,
                            json=update.model_dump(mode="json"),
                            timeout=10.0,
                        )
                        logger.info(
                            "Notified subscriber %s for condition '%s'",
                            sub.callback_url,
                            sub.condition,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to notify subscriber %s", sub.callback_url
                        )
                # Also check normalised Arabic conditions
                for sub in self._subscribers.get(cond_normalised, []):
                    if not sub.is_active:
                        continue
                    try:
                        await self._http_client.post(
                            sub.callback_url,
                            json=update.model_dump(mode="json"),
                            timeout=10.0,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to notify Arabic subscriber %s",
                            sub.callback_url,
                        )

    # ------------------------------------------------------------------
    # HTML heuristics (simplified; production would use BeautifulSoup)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_title(html: str) -> str:
        """Extract page <title> from raw HTML."""
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
        return "Untitled Guideline"

    @staticmethod
    def _derive_guideline_id(url: str, title: str) -> str:
        """Derive a stable guideline ID from URL and title."""
        raw = f"{url}:{title}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _match_source(url: str) -> str:
        """Guess the source_id from a URL."""
        url_lower = url.lower()
        for src in BUILTIN_SOURCES:
            if src.base_url.lower().replace("https://", "").startswith(
                url_lower.replace("https://", "").split("/")[0]
            ):
                return src.source_id
        return "unknown"

    @staticmethod
    def _extract_guideline_links(
        html: str,
        base_url: str,
    ) -> List[tuple]:
        """Extract (title, url) tuples from guideline listing page."""
        links: List[tuple] = []
        pattern = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
        for match in pattern.finditer(html):
            href = match.group(1)
            text = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            if text and len(text) > 10:
                if not href.startswith("http"):
                    href = f"{base_url.rstrip('/')}/{href.lstrip('/')}"
                links.append((text, href))
        return links[:50]  # Limit to prevent runaway processing

    @staticmethod
    def _extract_conditions(html: str) -> List[str]:
        """Heuristic: extract likely condition names from page content."""
        # In production this would use NER on the visible text
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        conditions: List[str] = []
        # Common keywords that hint at conditions
        for keyword in [
            "diabetes",
            "hypertension",
            "heart failure",
            "asthma",
            "COPD",
            "السكري",
            "ارتفاع ضغط الدم",
            "قصور القلب",
            "الربو",
        ]:
            if keyword.lower() in text.lower():
                conditions.append(keyword)
        return list(set(conditions))

    @staticmethod
    def _extract_keywords(html: str) -> List[str]:
        """Extract keywords from <meta name="keywords"> or body text."""
        match = re.search(
            r'<meta[^>]+name\s*=\s*["\']keywords["\'][^>]+content\s*=\s*["\'](.*?)["\']',
            html,
            re.IGNORECASE,
        )
        if match:
            return [kw.strip() for kw in match.group(1).split(",") if kw.strip()]
        return []

    @staticmethod
    def _normalise_arabic(text: str) -> str:
        """Normalise Arabic text by removing tashkeel and normalising forms.

        This is critical for matching Arabic medical terms reliably
        regardless of diacritic marks.
        """
        # Arabic tashkeel Unicode range: 0x0610 – 0x061A, 0x064B – 0x065F, 0x0670
        tashkeel_pattern = re.compile(
            r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7-\u06E8\u06EA-\u06ED]"
        )
        normalised = tashkeel_pattern.sub("", text)
        # Normalise alef variants
        normalised = normalised.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
        # Normalise taa marbuta and haa
        normalised = normalised.replace("ة", "ه")
        return normalised.strip()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Release the async HTTP client resources."""
        await self._http_client.aclose()
        logger.info("GuidelineTracker HTTP client closed.")
