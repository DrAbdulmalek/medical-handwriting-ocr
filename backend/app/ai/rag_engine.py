"""
Retrieval-Augmented Generation (RAG) Engine for Medical Documents.

Provides semantic search and question-answering over a corpus of medical
documents.  Builds a vector index from document chunks, supports hybrid search
(combining semantic similarity with keyword matching), and produces answers
with source attribution and citations.

Uses ChromaDB or FAISS as the vector store backend (selected via configuration)
and sentence-transformers for embeddings.  The LLM integration layer
(:class:`LLMIntegration`) handles the generation component.
"""

import logging
import uuid
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

import numpy as np
from pydantic import BaseModel, Field

from app.config import settings
from app.ai.chunker import MedicalTextChunker, Chunk, DocumentChunk
from app.ai.semantic_splitter import SemanticSplitter, SemanticChunk

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Data Models
# =============================================================================


class DocumentEmbedding(BaseModel):
    """Metadata for a document that has been embedded in the vector store."""

    document_id: str = Field(description="Source document UUID")
    chunk_id: str = Field(description="Chunk UUID")
    text: str = Field(description="Chunk text content")
    embedding_model: str = Field(default="", description="Model used to generate the embedding")
    embedding_dim: int = Field(default=0, description="Dimensionality of the embedding vector")
    token_count: int = Field(default=0, description="Approximate token count of the chunk")
    language: Optional[str] = Field(default=None, description="'ar', 'en', or 'mixed'")
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class RetrievalResult(BaseModel):
    """A single search result from the RAG engine."""

    chunk_id: str = Field(description="Chunk UUID")
    document_id: str = Field(description="Source document UUID")
    text: str = Field(description="Retrieved chunk text")
    score: float = Field(description="Relevance score (higher is better)")
    source_type: str = Field(default="semantic", description="'semantic', 'keyword', or 'hybrid'")
    page_number: Optional[int] = Field(default=None)
    section: Optional[str] = Field(default=None)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class QAAnswer(BaseModel):
    """Question-answer result with source attribution."""

    question: str = Field(description="The question asked")
    answer: str = Field(description="Generated answer")
    sources: List[RetrievalResult] = Field(description="Source chunks used to generate the answer")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Overall confidence")
    model_used: str = Field(default="", description="LLM model used for generation")
    tokens_used: Dict[str, int] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class IndexStats(BaseModel):
    """Statistics about the RAG vector index."""

    total_documents: int = Field(default=0)
    total_chunks: int = Field(default=0)
    embedding_model: str = Field(default="")
    embedding_dim: int = Field(default=0)
    index_size_bytes: int = Field(default=0, description="Approximate index size in bytes")
    languages: Dict[str, int] = Field(default_factory=dict, description="Language distribution: {'ar': n, 'en': m}")
    last_updated: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# =============================================================================
# Constants
# =============================================================================

_DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_DEFAULT_COLLECTION_NAME = "medical_documents"

_RERANK_WEIGHT_SEMANTIC = 0.7
_RERANK_WEIGHT_KEYWORD = 0.3


# =============================================================================
# MedicalRAGEngine
# =============================================================================


class MedicalRAGEngine:
    """
    Retrieval-Augmented Generation engine for medical documents.

    Workflow:
        1. **Indexing** — Accept documents, chunk them (via :class:`MedicalTextChunker`),
           compute embeddings, and store in a vector database (ChromaDB or FAISS).
        2. **Retrieval** — Given a query, embed it, search the vector store
           (semantic + optional keyword hybrid), and re-rank results.
        3. **Generation** — Pass retrieved context to an LLM to answer questions,
           with source attribution and citation.

    The engine is lazy-initialised: the embedding model and vector store
    are only loaded on first use.
    """

    def __init__(
        self,
        embedding_model: Optional[str] = None,
        collection_name: Optional[str] = None,
        chunker: Optional[MedicalTextChunker] = None,
        vector_store_backend: str = "chroma",
        persist_directory: Optional[str] = None,
    ):
        """
        Args:
            embedding_model: Sentence-transformers model name.
            collection_name: Name of the vector store collection.
            chunker: Custom :class:`MedicalTextChunker`.  Uses default if *None*.
            vector_store_backend: ``"chroma"`` or ``"faiss"``.
            persist_directory: Directory for persistent vector storage.
        """
        self.embedding_model_name = embedding_model or _DEFAULT_EMBEDDING_MODEL
        self.collection_name = collection_name or _DEFAULT_COLLECTION_NAME
        self.chunker = chunker or MedicalTextChunker()
        self.vector_store_backend = vector_store_backend
        self.persist_directory = persist_directory or "./data/vector_store"

        # Lazy-loaded state
        self._embedding_model = None
        self._embedding_model_loaded = False
        self._vector_store = None
        self._vector_store_loaded = False

        logger.info(
            "MedicalRAGEngine initialised (embedding=%s, backend=%s, collection=%s)",
            self.embedding_model_name,
            self.vector_store_backend,
            self.collection_name,
        )

    # ------------------------------------------------------------------
    # Lazy Loading
    # ------------------------------------------------------------------

    def _load_embedding_model(self) -> None:
        """Lazy-load the sentence-transformers embedding model."""
        if self._embedding_model_loaded:
            return

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers is required for MedicalRAGEngine. "
                "Install with: pip install sentence-transformers"
            )

        try:
            logger.info("Loading embedding model '%s' …", self.embedding_model_name)
            self._embedding_model = SentenceTransformer(self.embedding_model_name)
            self._embedding_model_loaded = True
            logger.info("Embedding model '%s' loaded successfully.", self.embedding_model_name)
        except Exception as exc:
            logger.error("Failed to load embedding model: %s", exc)
            raise RuntimeError(f"Cannot load embedding model: {exc}") from exc

    def _load_vector_store(self) -> None:
        """Lazy-load the vector store."""
        if self._vector_store_loaded:
            return

        import os
        os.makedirs(self.persist_directory, exist_ok=True)

        if self.vector_store_backend == "chroma":
            self._init_chroma()
        elif self.vector_store_backend == "faiss":
            self._init_faiss()
        else:
            raise ValueError(f"Unknown vector store backend: {self.vector_store_backend}")

        self._vector_store_loaded = True
        logger.info("Vector store ('%s') initialised at %s", self.vector_store_backend, self.persist_directory)

    def _init_chroma(self) -> None:
        """Initialise a ChromaDB vector store."""
        try:
            import chromadb
        except ImportError:
            raise ImportError(
                "chromadb is required for the 'chroma' backend. "
                "Install with: pip install chromadb"
            )

        client = chromadb.PersistentClient(path=self.persist_directory)
        self._vector_store = client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def _init_faiss(self) -> None:
        """Initialise a FAISS-based vector store (simplified in-memory wrapper)."""
        try:
            import faiss
        except ImportError:
            raise ImportError(
                "faiss-cpu is required for the 'faiss' backend. "
                "Install with: pip install faiss-cpu"
            )

        # Minimal FAISS wrapper stored as a dict on self
        self._vector_store = {
            "index": None,
            "documents": [],  # Parallel list of {id, text, document_id, metadata}
            "dimension": None,
        }

    # ------------------------------------------------------------------
    # Index Building
    # ------------------------------------------------------------------

    def build_index(self, documents: List[Dict[str, Any]]) -> IndexStats:
        """
        Build (or rebuild) the vector index from a list of documents.

        Each document dict should contain:
            * ``document_id`` (str)
            * ``text`` (str) — full document text
            * ``page_number`` (int, optional)
            * ``metadata`` (dict, optional)

        Args:
            documents: List of document dictionaries.

        Returns:
            An :class:`IndexStats` summary.
        """
        self._load_embedding_model()
        self._load_vector_store()

        total_chunks = 0
        languages: Dict[str, int] = {}

        all_chunks: List[DocumentChunk] = []
        for doc in documents:
            pages = [
                {
                    "page_number": doc.get("page_number", 1),
                    "text": doc.get("text", ""),
                    "page_id": doc.get("page_id"),
                    "document_id": doc.get("document_id"),
                }
            ]
            doc_chunks = self.chunker.chunk_document(pages)
            all_chunks.extend(doc_chunks)

        if not all_chunks:
            logger.warning("No chunks produced from %d documents", len(documents))
            return IndexStats()

        # Compute embeddings
        texts = [chunk.text for chunk in all_chunks]
        embeddings = self._embedding_model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

        # Store in vector store
        self._store_embeddings(all_chunks, embeddings, texts)

        # Compute stats
        for chunk in all_chunks:
            total_chunks += 1
            lang = chunk.metadata.language or "unknown"
            languages[lang] = languages.get(lang, 0) + 1

        stats = IndexStats(
            total_documents=len(documents),
            total_chunks=total_chunks,
            embedding_model=self.embedding_model_name,
            embedding_dim=embeddings.shape[1] if embeddings.ndim == 2 else 0,
            languages=languages,
        )

        logger.info(
            "Index built: %d docs → %d chunks (dim=%d, lang=%s)",
            stats.total_documents,
            stats.total_chunks,
            stats.embedding_dim,
            stats.languages,
        )
        return stats

    def add_documents(self, documents: List[Dict[str, Any]]) -> None:
        """
        Add documents to an existing index without rebuilding.

        Args:
            documents: List of document dictionaries.
        """
        self._load_embedding_model()
        self._load_vector_store()

        all_chunks: List[DocumentChunk] = []
        for doc in documents:
            pages = [
                {
                    "page_number": doc.get("page_number", 1),
                    "text": doc.get("text", ""),
                    "page_id": doc.get("page_id"),
                    "document_id": doc.get("document_id"),
                }
            ]
            all_chunks.extend(self.chunker.chunk_document(pages))

        if not all_chunks:
            return

        texts = [chunk.text for chunk in all_chunks]
        embeddings = self._embedding_model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

        self._store_embeddings(all_chunks, embeddings, texts)
        logger.info("Added %d chunks from %d documents to index", len(all_chunks), len(documents))

    def delete_document(self, doc_id: str) -> None:
        """
        Remove all chunks belonging to a specific document from the index.

        Args:
            doc_id: Document UUID to remove.
        """
        self._load_vector_store()

        if self.vector_store_backend == "chroma":
            try:
                self._vector_store.delete(
                    where={"document_id": doc_id},
                )
                logger.info("Deleted all chunks for document '%s' from ChromaDB", doc_id)
            except Exception as exc:
                logger.error("Failed to delete document '%s': %s", doc_id, exc)

        elif self.vector_store_backend == "faiss":
            store = self._vector_store  # type: ignore
            original_count = len(store["documents"])
            store["documents"] = [d for d in store["documents"] if d.get("document_id") != doc_id]
            removed = original_count - len(store["documents"])
            # Rebuild FAISS index
            self._rebuild_faiss_index(store)
            logger.info("Deleted %d chunks for document '%s' from FAISS", removed, doc_id)

    # ------------------------------------------------------------------
    # Search / Retrieval
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 5, search_type: str = "hybrid") -> List[RetrievalResult]:
        """
        Search the vector index for chunks relevant to *query*.

        Args:
            query: Search query text.
            top_k: Number of results to return.
            search_type: ``"semantic"``, ``"keyword"``, or ``"hybrid"`` (default).

        Returns:
            A list of :class:`RetrievalResult` sorted by relevance (descending).
        """
        self._load_embedding_model()
        self._load_vector_store()

        if search_type == "hybrid":
            results = self._hybrid_search(query, top_k)
        elif search_type == "semantic":
            results = self._semantic_search(query, top_k)
        elif search_type == "keyword":
            results = self._keyword_search(query, top_k)
        else:
            raise ValueError(f"Unknown search type: {search_type}")

        logger.debug("Search '%s' → %d results (type=%s)", query[:50], len(results), search_type)
        return results

    def answer_question(
        self,
        question: str,
        top_k: int = 5,
        search_type: str = "hybrid",
    ) -> QAAnswer:
        """
        Answer a medical question by retrieving relevant context and
        passing it to the LLM for generation.

        Args:
            question: The question to answer.
            top_k: Number of context chunks to retrieve.
            search_type: Search strategy (``"semantic"``, ``"keyword"``, ``"hybrid"``).

        Returns:
            A :class:`QAAnswer` with the generated answer and source citations.
        """
        # Retrieve context
        results = self.search(question, top_k=top_k, search_type=search_type)

        if not results:
            return QAAnswer(
                question=question,
                answer="No relevant documents found to answer this question.",
                sources=[],
                confidence=0.0,
            )

        # Build context from top results
        context_parts: List[str] = []
        for i, r in enumerate(results):
            context_parts.append(f"[{i + 1}] {r.text}")

        context = "\n\n".join(context_parts)

        # Use LLM to generate answer
        try:
            from app.ai.llm_integration import LLMIntegration

            llm = LLMIntegration()
            llm.initialize_llm()
            answer_text = llm.medical_qa(question, context)

            model_used = llm.config.model or llm.config.provider
        except Exception as exc:
            logger.error("LLM generation failed, returning context only: %s", exc)
            answer_text = "Could not generate answer. Here are the most relevant excerpts:\n\n" + context
            model_used = "fallback"

        # Compute confidence from retrieval scores
        confidence = float(np.mean([r.score for r in results])) if results else 0.0
        confidence = min(1.0, max(0.0, confidence))

        qa = QAAnswer(
            question=question,
            answer=answer_text,
            sources=results,
            confidence=round(confidence, 4),
            model_used=model_used,
        )

        logger.info("Q&A complete: confidence=%.3f, sources=%d", qa.confidence, len(qa.sources))
        return qa

    # ------------------------------------------------------------------
    # Internal Search Methods
    # ------------------------------------------------------------------

    def _semantic_search(self, query: str, top_k: int) -> List[RetrievalResult]:
        """Pure semantic similarity search."""
        query_embedding = self._embedding_model.encode([query], normalize_embeddings=True)[0]

        if self.vector_store_backend == "chroma":
            return self._chroma_search(query_embedding, top_k, "semantic")
        elif self.vector_store_backend == "faiss":
            return self._faiss_search(query_embedding, top_k, "semantic")
        return []

    def _keyword_search(self, query: str, top_k: int) -> List[RetrievalResult]:
        """Keyword-based (BM25-style) search using term frequency matching."""
        self._load_vector_store()

        query_terms = set(query.lower().split())
        results: List[RetrievalResult] = []

        if self.vector_store_backend == "chroma":
            # ChromaDB where-filter doesn't support full-text search natively,
            # so we do a broad retrieval and re-score
            all_results = self._chroma_search(
                self._embedding_model.encode([query], normalize_embeddings=True)[0],
                max(top_k * 5, 20),
                "keyword",
            )
            for r in all_results:
                doc_terms = set(r.text.lower().split())
                overlap = len(query_terms & doc_terms)
                score = overlap / len(query_terms) if query_terms else 0.0
                if score > 0.1:
                    r.score = score
                    r.source_type = "keyword"
                    results.append(r)

        elif self.vector_store_backend == "faiss":
            store = self._vector_store  # type: ignore
            for doc_entry in store["documents"]:
                doc_terms = set(doc_entry["text"].lower().split())
                overlap = len(query_terms & doc_terms)
                score = overlap / len(query_terms) if query_terms else 0.0
                if score > 0.1:
                    results.append(
                        RetrievalResult(
                            chunk_id=doc_entry["id"],
                            document_id=doc_entry.get("document_id", ""),
                            text=doc_entry["text"],
                            score=score,
                            source_type="keyword",
                            metadata=doc_entry.get("metadata", {}),
                        )
                    )

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def _hybrid_search(self, query: str, top_k: int) -> List[RetrievalResult]:
        """Combine semantic and keyword search results with re-ranking."""
        semantic_results = self._semantic_search(query, top_k * 2)
        keyword_results = self._keyword_search(query, top_k * 2)

        # Merge and re-rank
        merged: Dict[str, RetrievalResult] = {}

        for r in semantic_results:
            merged[r.chunk_id] = r

        for r in keyword_results:
            if r.chunk_id in merged:
                existing = merged[r.chunk_id]
                # Weighted combination
                existing.score = (
                    _RERANK_WEIGHT_SEMANTIC * existing.score
                    + _RERANK_WEIGHT_KEYWORD * r.score
                )
                existing.source_type = "hybrid"
            else:
                r.score = _RERANK_WEIGHT_KEYWORD * r.score
                merged[r.chunk_id] = r

        # Sort by combined score
        results = sorted(merged.values(), key=lambda r: r.score, reverse=True)
        return results[:top_k]

    # ------------------------------------------------------------------
    # Vector Store Operations
    # ------------------------------------------------------------------

    def _store_embeddings(
        self,
        chunks: List[DocumentChunk],
        embeddings: np.ndarray,
        texts: List[str],
    ) -> None:
        """Store chunk embeddings in the configured vector store."""
        if self.vector_store_backend == "chroma":
            self._store_chroma(chunks, embeddings, texts)
        elif self.vector_store_backend == "faiss":
            self._store_faiss(chunks, embeddings, texts)

    def _store_chroma(
        self,
        chunks: List[DocumentChunk],
        embeddings: np.ndarray,
        texts: List[str],
    ) -> None:
        """Store in ChromaDB."""
        ids = [chunk.id for chunk in chunks]
        metadatas = [
            {
                "document_id": chunk.document_id or "",
                "page_id": chunk.page_id or "",
                "page_number": chunk.metadata.page_number or 0,
                "section": chunk.metadata.section or "",
                "language": chunk.metadata.language or "unknown",
                "chunk_index": chunk.metadata.chunk_index,
            }
            for chunk in chunks
        ]

        # ChromaDB expects lists
        self._vector_store.add(
            ids=ids,
            embeddings=embeddings.tolist(),
            documents=texts,
            metadatas=metadatas,
        )

    def _store_faiss(
        self,
        chunks: List[DocumentChunk],
        embeddings: np.ndarray,
        texts: List[str],
    ) -> None:
        """Store in FAISS."""
        store = self._vector_store  # type: ignore

        # Build document entries
        for chunk, text in zip(chunks, texts):
            store["documents"].append(
                {
                    "id": chunk.id,
                    "text": text,
                    "document_id": chunk.document_id or "",
                    "page_id": chunk.page_id or "",
                    "metadata": {
                        "page_number": chunk.metadata.page_number,
                        "section": chunk.metadata.section,
                        "language": chunk.metadata.language,
                        "chunk_index": chunk.metadata.chunk_index,
                    },
                }
            )

        self._rebuild_faiss_index(store, embeddings)

    def _rebuild_faiss_index(self, store: dict, new_embeddings: Optional[np.ndarray] = None) -> None:
        """Rebuild the FAISS index from stored documents."""
        import faiss

        if not store["documents"]:
            store["index"] = None
            return

        if new_embeddings is not None:
            all_embeddings = new_embeddings
        else:
            # Re-encode all documents (expensive but correct)
            texts = [d["text"] for d in store["documents"]]
            all_embeddings = self._embedding_model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

        dimension = all_embeddings.shape[1]
        store["dimension"] = dimension

        # Normalise for cosine similarity
        norms = np.linalg.norm(all_embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        all_embeddings = all_embeddings / norms

        index = faiss.IndexFlatIP(dimension)  # Inner product = cosine for normalised vectors
        index.add(all_embeddings.astype(np.float32))
        store["index"] = index

    def _chroma_search(
        self,
        query_embedding: np.ndarray,
        top_k: int,
        source_type: str,
    ) -> List[RetrievalResult]:
        """Search ChromaDB and return results."""
        try:
            results = self._vector_store.query(
                query_embeddings=[query_embedding.tolist()],
                n_results=min(top_k, self._vector_store.count()),
            )
        except Exception as exc:
            logger.error("ChromaDB query failed: %s", exc)
            return []

        if not results or not results["ids"]:
            return []

        retrieval_results: List[RetrievalResult] = []
        for i in range(len(results["ids"][0])):
            retrieval_results.append(
                RetrievalResult(
                    chunk_id=results["ids"][0][i],
                    document_id=results["metadatas"][0][i].get("document_id", ""),
                    text=results["documents"][0][i],
                    score=float(results["distances"][0][i]) if results["distances"] else 0.0,
                    source_type=source_type,
                    page_number=results["metadatas"][0][i].get("page_number"),
                    section=results["metadatas"][0][i].get("section"),
                    metadata=results["metadatas"][0][i],
                )
            )

        retrieval_results.sort(key=lambda r: r.score, reverse=True)
        return retrieval_results[:top_k]

    def _faiss_search(
        self,
        query_embedding: np.ndarray,
        top_k: int,
        source_type: str,
    ) -> List[RetrievalResult]:
        """Search FAISS index and return results."""
        store = self._vector_store  # type: ignore

        if store["index"] is None or not store["documents"]:
            return []

        # Normalise query
        query_norm = query_embedding / (np.linalg.norm(query_embedding) + 1e-8)
        query_np = np.array([query_norm], dtype=np.float32)

        k = min(top_k, store["index"].ntotal)
        distances, indices = store["index"].search(query_np, k)

        results: List[RetrievalResult] = []
        for i in range(k):
            idx = int(indices[0][i])
            if idx < 0 or idx >= len(store["documents"]):
                continue
            doc_entry = store["documents"][idx]
            results.append(
                RetrievalResult(
                    chunk_id=doc_entry["id"],
                    document_id=doc_entry.get("document_id", ""),
                    text=doc_entry["text"],
                    score=float(distances[0][i]),
                    source_type=source_type,
                    metadata=doc_entry.get("metadata", {}),
                )
            )

        return results

    # ------------------------------------------------------------------
    # Index Stats
    # ------------------------------------------------------------------

    def get_index_stats(self) -> IndexStats:
        """
        Return current index statistics.

        Returns:
            An :class:`IndexStats` object.
        """
        self._load_vector_store()

        total_chunks = 0
        total_docs = 0
        languages: Dict[str, int] = {}
        dimension = 0

        if self.vector_store_backend == "chroma":
            total_chunks = self._vector_store.count()
            try:
                peek = self._vector_store.peek(limit=min(total_chunks, 1000))
                if peek and peek["metadatas"]:
                    doc_ids = set()
                    lang_map: Dict[str, int] = {}
                    for meta in peek["metadatas"]:
                        did = meta.get("document_id", "")
                        if did:
                            doc_ids.add(did)
                        lang = meta.get("language", "unknown")
                        lang_map[lang] = lang_map.get(lang, 0) + 1
                    total_docs = len(doc_ids)
                    languages = lang_map
            except Exception:
                pass

        elif self.vector_store_backend == "faiss":
            store = self._vector_store  # type: ignore
            total_chunks = len(store["documents"])
            doc_ids = set()
            for d in store["documents"]:
                did = d.get("document_id")
                if did:
                    doc_ids.add(did)
                lang = d.get("metadata", {}).get("language", "unknown")
                languages[lang] = languages.get(lang, 0) + 1
            total_docs = len(doc_ids)
            dimension = store.get("dimension", 0)

        return IndexStats(
            total_documents=total_docs,
            total_chunks=total_chunks,
            embedding_model=self.embedding_model_name,
            embedding_dim=dimension,
            languages=languages,
        )
