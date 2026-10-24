from pydantic_settings import BaseSettings

class AppSettings(BaseSettings):
    LLM_PROVIDER: str = "anthropic"
    OPENAI_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    GROQ_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    GEMINI_MODEL: str = "gemini-1.5-flash"
    GROQ_MODEL: str = "llama3-70b-8192"
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    TEMPERATURE: float = 0.2
    MAX_TOKENS: int = 1500
    context_builder_prompt_path: str = "context_builder_prompt.yml"
    text2sql_generator_prompt_path: str = "text2sql_generator.yml"
    suprivisor_prompt_path: str = "suprivisor_prompt.yml"
    response_generator_prompt_path: str = "response_generator_prompt.yml"
    sql_correction_prompt_path: str = "sql_correction.yml"
    visualization_agent_prompt_path: str = "visualization_agent_prompt.yml"
    history_summarizer_prompt_path: str = "history_summarizer_prompt.yml"
    help_center_prompt_path: str = "help_center_prompt.yml"
    db_path: str = "transactions.db"
    EMBED_MODEL: str = "text-embedding-3-small"
    # Barclays help-centre RAG knowledge base (built by src/clients/barclays_help_ingest.py)
    help_store_dir: str = "help_store"
    help_collection: str = "barclays_help"
    help_top_k: int = 5
    help_similarity_threshold: float = 0.25
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_TTL: int = 1800
    # When a brand-new conversation starts, seed the context builder with the
    # customer's messages from the last N hours (rolling window). 0 disables it.
    # Only the most recent HISTORY_LOOKBACK_MAX_MESSAGES within that window are used.
    HISTORY_LOOKBACK_HOURS: int = 24
    HISTORY_LOOKBACK_MAX_MESSAGES: int = 5

    LANGCHAIN_API_KEY: str | None = None
    LANGCHAIN_TRACING_V2: str = "false"
    LANGCHAIN_PROJECT: str = "fintell-chatbot"
