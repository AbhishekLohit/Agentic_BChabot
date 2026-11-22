from pydantic_settings import BaseSettings

class AppSettings(BaseSettings):
    LLM_PROVIDER: str = "anthropic"
    OPENAI_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    GROQ_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    GEMINI_MODEL: str = "gemini-3.8-flash"
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    OLLAMA_MODEL: str = "llama3.2:3b"  # local model; must be pulled with `ollama pull`
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    # IIT KGP capstone Qwen3-8B API (LLM_PROVIDER=capstone). Set these in .env.
    CAPSTONE_API_KEY: str | None = None
    CAPSTONE_BASE_URL: str | None = None  # your group's URL, ending in /v1
    CAPSTONE_CA_CERT: str | None = None  # full path to your group's .crt file
    CAPSTONE_MODEL: str = "Qwen/Qwen3-8B"
    CAPSTONE_DISABLE_THINKING: bool = True
    # IIT KGP capstone embedding API (EMBEDDING_PROVIDER=capstone). Set these in .env.
    CAPSTONE_EMBED_API_KEY: str | None = None
    CAPSTONE_EMBED_BASE_URL: str = "https://103.42.50.41:1035/v1"
    CAPSTONE_EMBED_CA_CERT: str | None = None  # path to vm02-ca.crt (relative to the project root is fine)
    CAPSTONE_EMBED_MODEL: str = "bge-small-en-v1.5"
    # Optional BGE query prefix, e.g. "Represent this sentence for searching relevant passages: "
    # (applied to search queries only, never to documents). Empty = off.
    CAPSTONE_EMBED_QUERY_INSTRUCTION: str = ""
    TEMPERATURE: float = 0.2
    MAX_TOKENS: int = 1500
    context_builder_prompt_path: str = "context_builder_prompt.yml"
    help_agent_prompt_path: str = "help_agent_prompt.yml"
    text2sql_entity_extractor_prompt_path: str = "text2sql_entity_extractor_prompt.yml"
    text2sql_generator_prompt_path: str = "text2sql_generator.yml"
    suprivisor_prompt_path: str = "suprivisor_prompt.yml"
    response_generator_prompt_path: str = "response_generator_prompt.yml"
    fallback_prompt_path: str = "fallback_prompt.yml"
    sql_correction_prompt_path: str = "sql_correction.yml"
    test_data_generation_prompt_path: str = "test_data_generation_prompt.yml"
    test_data_validation_prompt_path: str = "test_data_validation_prompt.yml"
    visualization_agent_prompt_path: str = "visualization_agent_prompt.yml"
    db_path: str = "transactions.db"
    EMBED_MODEL: str = "text-embedding-3-small"  # used only when EMBEDDING_PROVIDER = "openai"
    EMBEDDING_PROVIDER: str = "local"  # "local" (MiniLM, offline), "capstone" (bge-small API) or "openai"
    HELP_DOCUMENTS_DIR: str = "data/help_documents"
    HELP_CHROMA_DIR: str = "chroma_store/help_documents"
    # Help-document retrieval (see src/utils/help_rag.py). Compare modes with
    # eval/rag/retrieval_experiments.py before switching.
    HELP_RETRIEVAL_MODE: str = "dense"  # "dense", "bm25", "hybrid" or "hybrid_rerank"
    HELP_TOP_K: int = 4  # chunks passed to the LLM (keep small: Qwen's window is 8K tokens)
    HELP_RETRIEVAL_CANDIDATES: int = 30  # per retriever for hybrid; pool the reranker re-scores
    RERANKER_MODEL: str = "ms-marco-MiniLM-L-12-v2"  # flashrank cross-encoder (ONNX, no PyTorch)
    HELP_CITATION_POLICY: str = "strict"  # "off", "check" or "strict" (see help_rag.enforce_citations)
    AGENT_RECURSION_LIMIT: int = 10  # max LangGraph steps per question
    HELP_CHUNK_STRATEGY: str = "fixed"  # "fixed", "structure" or "structure_header"; rebuild the index after changing

    merchant_name_query: str = "SELECT DISTINCT merchant_brand_name FROM transactions WHERE customer_id = ?;"
    merchant_category1_query: str = "SELECT DISTINCT merchant_system_category1_name FROM transactions WHERE customer_id = ?;"
    merchant_category2_query: str = "SELECT DISTINCT merchant_system_category2_name FROM transactions WHERE customer_id = ?;"
    transaction_country_query: str = "SELECT DISTINCT country FROM transactions WHERE customer_id = ?;"
    transaction_channel_query: str = "SELECT DISTINCT channel FROM transactions WHERE customer_id = ?;"

    MERCHANT_CATEGORIES: list = [
        "Shopping", "Eating Out", "Groceries", "Transport", "Bills",
        "Services", "Healthcare", "Personal Care", "General", "Travel",
        "Automotive", "Entertainment", "Charity"
    ]

    TRANSACTION_COUNTRIES: list = [
        "Nigeria", "UK", "Mexico", "Russia", "Australia",
        "France", "Brazil", "Canada", "Germany", "Japan",
        "Singapore", "USA"
    ]

    CARD_TYPES: list = [
        "Basic Debit", "Premium Debit"
    ]

    TRANSACTION_CHANNELS: list = [
        "mobile", "web", "pos"
    ]
