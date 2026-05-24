import sqlite3
import pandas as pd
from typing import List, Dict

from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

from src.clients.llm_client import get_openai_embeddings
from src.app_setting import AppSettings
import time 


class ChromaEmbeddingStore:
    """
    Production-ready class to generate embeddings and store them in Chroma.
    Stores all entities inside a single Chroma collection with metadata filtering.
    """

    def __init__(
        self,
        persist_directory: str = "chroma_store",
        model: str = "text-embedding-3-small",
        top_k: int = 5,
        threshold: float = 0.10
    ):
        self.top_k = top_k
        self.threshold = threshold

        # Embedding client (LangChain wrapper)
        self.client = get_openai_embeddings(model=model)

        # Chroma vector store
        self.vector_store = Chroma(
            collection_name="entities",
            embedding_function=self.client,
            persist_directory=persist_directory
        )

    # ---------------------------------------------------------
    # STORE ENTITIES
    # ---------------------------------------------------------
    def add_entities(self, texts: List[str], entity_type: str):
        """
        Stores a list of strings into Chroma with metadata.
        Automatically avoids duplicate insertions.
        """

        if not texts:
            return

        batch_size = 500  # safe limit
        metadata_list = [{"entity_type": entity_type} for _ in texts]

        print(f"📌 Adding {len(texts)} items for entity '{entity_type}' in batches...")

        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i : i + batch_size]
            batch_metadata = metadata_list[i : i + batch_size]

            self.vector_store.add_texts(
                texts=batch_texts,
                metadatas=batch_metadata
            )

        print(f"✅ Finished storing entity '{entity_type}'")
    # ---------------------------------------------------------
    # SEARCH
    # ---------------------------------------------------------
    def search(self, query: str, entity_type: str):
        """
        Search inside Chroma for items of a specific entity type.
        Returns (text, similarity) tuples.
        """
        query = query.lower().strip()
        results = self.vector_store.similarity_search_with_score(
            query,
            k=self.top_k,
            filter={"entity_type": entity_type}
        )
        print(f"Raw results: {results}")
        final = []
        for doc, distance in results:
            similarity = 1 - distance

            if similarity >= self.threshold:
                final.append((doc.page_content, similarity))

        return final


# ============================================================================
# MAIN ORCHESTRATION FUNCTION — CALL THIS FROM ANYWHERE
# ============================================================================

def build_entity_embeddings():
    """
    Main entry point:
    Fetches entities from DB → generates embeddings → stores in Chroma.
    No FAISS, no CSVs. Fully automated and idempotent.
    """

    settings = AppSettings()
    conn = sqlite3.connect(settings.db_path)

    print("🔍 Fetching data from database...")

    # Extract data from DB
    order_items_df = pd.read_sql_query("SELECT food_item FROM order_items;", conn)
    restaurants_df = pd.read_sql_query(
        "SELECT name, city, type, primary_cuisine FROM restaurants;",
        conn
    )
    conn.close()

    # Convert to Python lists
    data: Dict[str, List[str]] = {
        "order_item_name": order_items_df["food_item"].dropna().str.lower().str.strip().unique().tolist(),
        "restaurant_name": restaurants_df["name"].dropna().str.lower().str.strip().unique().tolist(),
        "restaurant_city": restaurants_df["city"].dropna().str.lower().str.strip().unique().tolist(),
        "restaurant_type": restaurants_df["type"].dropna().str.lower().str.strip().unique().tolist(),
        "restaurant_cuisine": restaurants_df["primary_cuisine"].dropna().str.lower().str.strip().unique().tolist(),
    }

    print("✨ Initializing Chroma store...")
    store = ChromaEmbeddingStore(
        persist_directory="chroma_store",
        top_k=5,
        threshold=0.10
    )

    # 👉 FULL RESET
    print("🧹 Clearing old Chroma data...")
    store.vector_store.reset_collection()

    # Store all entities
    print("📦 Storing entities in vector DB...")
    for entity_type, items in data.items():
        store.add_entities(items, entity_type)

    print("✅ Embeddings built and stored successfully!")
    return store  # optional: return for immediate querying


# ============================================================================
# OPTIONAL TEST FUNCTION
# ============================================================================

def test_query():
    """
    Small demo to test searching.
    """
    store = ChromaEmbeddingStore(
        persist_directory="chroma_store",
        top_k=5,
        threshold=0.10
    )
    results = store.search("Masala Dosa", "order_item_name")
    print("🔍 Query results:")
    for text, score in results:
        print(f"- {text}  (similarity={score:.3f})")


if __name__ == "__main__":
    # build_entity_embeddings()
    test_query()
