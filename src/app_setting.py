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
    EMBED_MODEL: str = "text-embedding-3-small"
    REDIS_URL: str = "redis://localhost:6379/0"

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
