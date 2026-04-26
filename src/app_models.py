from typing import Literal
from pydantic import BaseModel,Field, field_validator
from langchain_core.messages import BaseMessage
from datetime import datetime
from langchain_core.prompts import BasePromptTemplate


class Prompts(BaseModel):
    context_builder_prompt: BasePromptTemplate
    suprivisor_prompt: BasePromptTemplate
    text2sqlgenerator_prompt: BasePromptTemplate
    responsegenerator_prompt: BasePromptTemplate
    fallback_prompt: BasePromptTemplate
    sql_correction_prompt: BasePromptTemplate
    test_data_generation_prompt: BasePromptTemplate
    

class UIUserRequest(BaseModel):
    customer_id: str
    session_id: str
    user_query: str



class GuardRailResponse(BaseModel):
    guardrail_invoked : str | None
    guardrail_reason : str | None

class ContextBuilderResponse(BaseModel):
    user_query : str

class ClassifiedSubQuery(BaseModel):
    sub_query: str
    class_label: Literal["BankSQL","Out_of_scope"]

class SuprivisorResponse(BaseModel):
    classified_sub_queries : list[ClassifiedSubQuery]

class SqlCorrectionResponse(BaseModel):
    corrected_sql_query: str

class FallbackResponse(BaseModel):
    fallback_flag: bool = False
    fallback_node : str | None
    fallback_reason : str | None
    fallback_message: str | None

class FallbackNodeResponse(BaseModel):
    fallback_message: str

class SQlSmithResponse(BaseModel):
    data: str
    sql_query: str

class FinRagResponse(BaseModel):
    rag_text: list[str]
    similiarity_score: list[float]

class SummarizationResponse(BaseModel):
    summarized_text : str

class ResponseGeneratorRequest(BaseModel):
    input_data: str | None
    fallback_flag : str | None
    sqlsmith_flag : str | None
    finrag_flag : str | None
    summarization_flag: str | None

class ResponseGeneratorResponse(BaseModel):
    generated_response: str

class OutputGuardrail(BaseModel):
    final_response: str
    guardrail_invoked: str
    guardrail_reasoning: str

class ChatMessage(BaseModel):
    role: Literal["user","assistant"]
    content: str

class UIUserRequestDebug(BaseModel):
    user_query: str
    customer_id: str
    session_id: str
    chat_history: list[ChatMessage]

class EntityExtraction(BaseModel):
    user_query: str
    merchant_names: list[str]
    merchant_categories: list[str]
    countries: list[str]
    channels: list[str]
    card_types: list[str]


class Text2SqlEntityExtractionResponse(BaseModel):
    entity_extraction: list[EntityExtraction]

class Text2SqlGeneratorResponse(BaseModel):
    user_query: str
    sql_query: str

class Metrics(BaseModel):
    model_used: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_tokens: int
    latency: float
    model_cost_without_cache: float
    model_cost_with_cache: float
    @field_validator(
        "latency",
        "model_cost_without_cache",
        "model_cost_with_cache",
        mode="before"
    )
    def round_to_2_decimals(cls, v):
        return round(float(v), 5)

class AgentMetrics(BaseModel):
    agents_metrics: dict[str, Metrics]
    # context_builder_metrics : Metrics | None
    # suprivisor_metrics : Metrics | None
    # classification_metrics : Metrics | None
    # text2sql_generator_metrics : Metrics | None
    # sql_corection_metrics : Metrics | None
    # response_generator_metrics : Metrics | None

class Graph_State(BaseModel):
    input_request: UIUserRequest | None
    chat_history: list[ChatMessage] |   None
    customer_id: str | None
    context_builder_response: ContextBuilderResponse | None
    entity_extraction_response: Text2SqlEntityExtractionResponse | None
    suprivisor_response: SuprivisorResponse | None
    text2sql_generator_response: Text2SqlGeneratorResponse | None
    database_query_results: list[str] | None
    sql_query_correction: bool = False
    banksql_queries: list[str] | None
    out_of_scope_queries: list[str] | None
    fallback_response: FallbackResponse | None
    response_generation_response: ResponseGeneratorResponse | None
    agent_metrics: dict[str, Metrics]  | None

class SubGraph_State(BaseModel):
    text2sql_generator_response: Text2SqlGeneratorResponse | None
    database_query_results: list[str] | None
    sql_query_correction: bool = False
    fallback_response: FallbackResponse | None
    banksql_queries: list[str] | None
    input_request: UIUserRequest | None
    agent_metrics: dict[str, Metrics]  | None

class Generated_Test_Data(BaseModel):
    chat_history: list[ChatMessage]
    latest_user_question: str
    

class Test_Data_Generation_Response(BaseModel):
    generated_test_data: list[Generated_Test_Data]
    