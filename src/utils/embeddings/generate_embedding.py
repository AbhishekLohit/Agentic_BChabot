import sqlite3
import pandas as pd
import numpy as np
import faiss
from src.clients.openai_client import get_openai_embeddings
from src.app_setting import AppSettings
from langchain_openai import OpenAIEmbeddings
from sqlite3 import Connection 
from logging import Logger
import time

class EmbeddingRecord:
    def __init__(self, 
                 conn: Connection,
                 client: OpenAIEmbeddings,
                 top_k = 5,
                 threshold = 0.50,
        ):
        self.conn = conn
        self.client = client
        self.top_k = top_k
        self.threshold = threshold

    def get_embeddings(self,
                       texts: list[str]
    ):
        vectors = self.client.embed_documents(texts)  # LangChain handles batching
        vectors = np.array(vectors, dtype="float32")

        # ✅ Normalize for cosine similarity
        faiss.normalize_L2(vectors)
        return vectors


    def create_faiss_index(self,
                           embeddings: np.ndarray, 
                           index_name: str
    ):
        dim = embeddings.shape[1]

        # ✅ Cosine similarity = Inner Product index
        index = faiss.IndexFlatIP(dim)

        index.add(embeddings)
        faiss.write_index(index, f"{index_name}.index")

        print(f"Saved FAISS cosine index '{index_name}.index' with {index.ntotal} vectors.")
        return index

    def generate_embeddings(self
    ) -> np.ndarray:
        order_items_df = pd.read_sql_query("SELECT food_item FROM order_items;", self.conn)
        orders_df = pd.read_sql_query("SELECT name,city,type,primary_cuisine FROM restaurants;", self.conn)

        order_items = order_items_df["food_item"].dropna().str.lower().str.strip().unique().tolist()
        restaurant_names = orders_df["name"].dropna().str.lower().str.strip().unique().tolist()
        restaurant_cities = orders_df["city"].dropna().str.lower().str.strip().unique().tolist()
        restaurant_type = orders_df["type"].dropna().str.lower().str.strip().unique().tolist()
        restaurant_cuisine = orders_df["primary_cuisine"].dropna().str.lower().str.strip().unique().tolist()

        self.conn.close()

        print("Creating embeddings for order items...")
        item_embeddings = self.get_embeddings(order_items)

        print("Creating embeddings for restaurant names...")
        rest_embeddings = self.get_embeddings(restaurant_names)

        print("Creating embeddings for restaurant cities...")
        city_embeddings = self.get_embeddings(restaurant_cities)

        print("Creating embeddings for restaurant types...")
        type_embeddings = self.get_embeddings(restaurant_type)  

        print("Creating embeddings for restaurant cuisines...")
        cuisine_embeddings = self.get_embeddings(restaurant_cuisine)    

        # Create cosine FAISS indexes
        self.create_faiss_index(item_embeddings, "order_item_name_index")
        self.create_faiss_index(rest_embeddings, "restaurant_name_index")
        self.create_faiss_index(city_embeddings, "restaurant_city_index")
        self.create_faiss_index(type_embeddings, "restaurant_type_index")
        self.create_faiss_index(cuisine_embeddings, "restaurant_cuisine_index")

        # Save metadata
        pd.DataFrame({"order_item_name": order_items}).to_csv("order_item_name_meta.csv", index=False)
        pd.DataFrame({"restaurant_name": restaurant_names}).to_csv("restaurant_name_meta.csv", index=False)
        pd.DataFrame({"restaurant_city": restaurant_cities}).to_csv("restaurant_city_meta.csv", index=False)
        pd.DataFrame({"restaurant_type": restaurant_type}).to_csv("restaurant_type_meta.csv", index=False)
        pd.DataFrame({"restaurant_cuisine": restaurant_cuisine}).to_csv("restaurant_cuisine_meta.csv", index=False)

        print("Metadata saved for all five entities.")

    # def search_in_index(self,
    #                     query:str, 
    #                     index:any, 
    #                     texts:list[str]                   
    # ) -> list[tuple[str, float]]:
    #     query_emb = self.client.embed_query(query)
    #     query_emb = np.array([query_emb], dtype="float32")

    #     # Normalize query embedding for cosine
    #     faiss.normalize_L2(query_emb)

    #     similarity_scores, indices = index.search(query_emb, self.top_k)
    #     results = []
    #     print(self.threshold)
    #     for j, idx in enumerate(indices[0]):
    #         score = float(similarity_scores[0][j])
    #           # cosine similarity (-1 to 1)
            
    #         if score >= self.threshold:
    #             #print(f"j: {j}, idx: {idx}, score: {score},texts[idx]: {texts[idx]}") 
    #             results.append((texts[idx], score))

    #     return results



if __name__ == "__main__":
    # -----------------------------
    # Setup
    # -----------------------------
    settings = AppSettings()
    conn = sqlite3.connect(settings.db_path)

    # -----------------------------
    # Generate Embeddings
    # -----------------------------
    embedding_client = get_openai_embeddings(model="text-embedding-3-small")
    embedding_generator = EmbeddingRecord(
        conn=conn,
        client=embedding_client,
        top_k=5,
        threshold=0.50
    )
    # embedding_generator.generate_embeddings()

    # -----------------------------
    # Example Test
    # -----------------------------
    index= faiss.read_index(r"src\utils\embeddings\data\index\order_item_name_index.index")
    df = pd.read_csv(r"src\utils\embeddings\data\metadata\order_item_name_meta.csv")
    order_items = df["order_item_name"].tolist()
    seach_text = "Masla Dsa"
    s_time = time.time()
    results = embedding_generator.search_in_index(seach_text.lower().strip(), index, order_items)
    e_time = time.time()
    print(f"Search Time: {e_time - s_time} seconds")
    print(results)
