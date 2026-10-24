"""
help_store.py

Thin wrapper around a dedicated Chroma collection holding the Barclays help-centre
knowledge base. Used both by the offline ingest script
(src/clients/barclays_help_ingest.py) and at runtime by the help_center agent.

Imports assume `src/` is on sys.path (true when the app is run via `python src/app.py`;
the ingest script adds it explicitly).
"""

from typing import Any

from langchain_chroma import Chroma

from clients.llm_client import get_openai_embeddings


class HelpStore:
    """Persistent Chroma store for Barclays help articles (chunked + embedded)."""

    def __init__(
        self,
        persist_directory: str = "help_store",
        collection_name: str = "barclays_help",
        model: str = "text-embedding-3-small",
        top_k: int = 5,
        threshold: float = 0.25,
    ):
        self.top_k = top_k
        self.threshold = threshold

        self.embeddings = get_openai_embeddings(model=model)
        self.vector_store = Chroma(
            collection_name=collection_name,
            embedding_function=self.embeddings,
            persist_directory=persist_directory,
            # cosine distance so `similarity = 1 - distance` is a true cosine similarity
            collection_metadata={"hnsw:space": "cosine"},
        )

    # ------------------------------------------------------------------
    # WRITE (ingest-time)
    # ------------------------------------------------------------------
    def add_documents(
        self,
        texts: list[str],
        metadatas: list[dict[str, Any]],
        batch_size: int = 200,
    ) -> None:
        """Embed and store chunks with their metadata (source_url, title)."""
        if not texts:
            return
        for i in range(0, len(texts), batch_size):
            self.vector_store.add_texts(
                texts=texts[i : i + batch_size],
                metadatas=metadatas[i : i + batch_size],
            )

    def reset(self) -> None:
        """Drop everything in the collection (used for a full rebuild)."""
        self.vector_store.reset_collection()

    def count(self) -> int | None:
        try:
            return self.vector_store._collection.count()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # READ (runtime)
    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        top_k: int | None = None,
        threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return the most similar help chunks above the similarity threshold.

        Each result is a dict: {text, source_url, title, similarity}.
        Returns [] when nothing clears the threshold.
        """
        k = top_k or self.top_k
        thr = self.threshold if threshold is None else threshold

        results = self.vector_store.similarity_search_with_score(query.strip(), k=k)

        final: list[dict[str, Any]] = []
        for doc, distance in results:
            similarity = 1 - distance
            if similarity >= thr:
                final.append(
                    {
                        "text": doc.page_content,
                        "source_url": doc.metadata.get("source_url"),
                        "title": doc.metadata.get("title"),
                        "similarity": round(float(similarity), 4),
                    }
                )
        return final
