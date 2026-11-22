import math
from typing import Literal
from pydantic import BaseModel, field_validator, model_validator
from langchain_core.prompts import BasePromptTemplate


class Prompts(BaseModel):
    context_builder_prompt: BasePromptTemplate
    suprivisor_prompt: BasePromptTemplate
    text2sqlgenerator_prompt: BasePromptTemplate
    responsegenerator_prompt: BasePromptTemplate
    sql_correction_prompt: BasePromptTemplate
    history_summarizer_prompt: BasePromptTemplate
    help_center_prompt: BasePromptTemplate


class UIUserRequest(BaseModel):
    customer_id: str
    session_id: str
    user_query: str



class GuardRailResponse(BaseModel):
    guardrail_invoked : str | None
    guardrail_reason : str | None

class ContextBuilderResponse(BaseModel):
    # The context builder is the ONLY splitter: it returns one or more fully
    # self-contained queries. Same-topic questions (e.g. multiple timeframes) stay
    # as a single entry; genuinely independent questions become separate entries.
    contextual_queries : list[str]

class ClassifiedSubQuery(BaseModel):
    sub_query: str
    class_label: Literal["BankSQL","Out_of_scope","HelpCenter"]
    transaction_query_type: Literal[
        "income", "payment", "direct_debit", "standing_order",
        "refund", "balance_check", "general", "none"
    ]

class SuprivisorResponse(BaseModel):
    classified_sub_queries : list[ClassifiedSubQuery]

class SqlCorrectionResponse(BaseModel):
    corrected_sql_query: str

class FallbackResponse(BaseModel):
    fallback_flag: bool = False
    fallback_node : str | None
    fallback_reason : str | None
    fallback_message: str | None

class ResponseGeneratorResponse(BaseModel):
    generated_response: str

class VisualizationDecision(BaseModel):
    should_visualize: bool
    chart_type: str  # "bar" | "line" | "pie" | "table" | "none"
    chart_title: str
    x_field: str | None
    y_field: str | None
    reasoning: str

class VisualizationResponse(BaseModel):
    decision: VisualizationDecision
    plotly_json: str | None

# --- Transaction Charts output contract (spine AD-19) -----------------------
# SkipReason is the single source of the nine FR-3 labels; every consumer
# imports it from here. Order mirrors the AD-20 decision table.
SkipReason = Literal[
    "chart_error",
    "fallback",
    "no_transaction_data",
    "empty_result",
    "raw_list",
    "too_few_points",
    "unsuitable_shape",
    "too_many_categories",
    "too_many_time_points",
]


class ChartPoint(BaseModel):
    # label is the display label ("Mar 2026", "Wk 12 2026", "Dining").
    # value is USD, finite, rounded to 2 decimals; net/balance may be negative.
    label: str
    value: float

    @field_validator("value")
    def value_finite_2dp(cls, v):
        if not math.isfinite(v):
            raise ValueError("ChartPoint.value must be finite")
        return round(v, 2)


class ChartSpec(BaseModel):
    # Points arrive in plot order; the UI never sorts or parses labels.
    chart_type: Literal["bar", "line"]
    title: str
    x_label: str
    y_label: str
    points: list[ChartPoint]


class ChartResponse(BaseModel):
    decision: Literal["bar", "line", "none"]
    skip_reason: SkipReason | None = None
    explicit_request: bool = False
    spec: ChartSpec | None = None

    @model_validator(mode="after")
    def check_decision_consistency(self):
        if self.decision == "none":
            if self.skip_reason is None:
                raise ValueError("skip_reason is required when decision is 'none'")
            if self.spec is not None:
                raise ValueError("spec is only allowed when decision is 'bar' or 'line'")
        else:
            if self.skip_reason is not None:
                raise ValueError("skip_reason is only allowed when decision is 'none'")
            if self.spec is None:
                raise ValueError("spec is required when decision is 'bar' or 'line'")
            if self.spec.chart_type != self.decision:
                raise ValueError("spec.chart_type must match decision")
        return self


class ChatMessage(BaseModel):
    role: Literal["user","assistant"]
    content: str

class Text2SqlGeneratorResponse(BaseModel):
    user_query: str
    sql_query: str
    # When the input contains MORE THAN ONE distinct intent, this flags whether all of
    # them can be answered by a single SQL statement. True for single-intent inputs.
    # When False, sql_query is empty and the flow falls back asking the user to split.
    can_answer_with_single_query: bool = True

class Metrics(BaseModel):
    model_used: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_tokens: int
    cache_creation_tokens: int = 0
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

class Graph_State(BaseModel):
    input_request: UIUserRequest | None
    chat_history: list[ChatMessage] |   None
    customer_id: str | None
    context_builder_response: ContextBuilderResponse | None
    suprivisor_response: SuprivisorResponse | None
    text2sql_generator_response: Text2SqlGeneratorResponse | None
    database_query_results: list[str] | None
    sql_query_correction: bool = False
    banksql_queries: list[str] | None
    out_of_scope_queries: list[str] | None
    help_center_queries: list[str] | None = None
    help_center_response: ResponseGeneratorResponse | None = None
    fallback_response: FallbackResponse | None
    response_generation_response: ResponseGeneratorResponse | None
    visualization_response: VisualizationResponse | None = None
    # Written only by chart_builder (AD-19); deliberately absent from SubGraph_State.
    chart_response: ChartResponse | None = None
    agent_metrics: dict[str, Metrics]  | None

class SubGraph_State(BaseModel):
    text2sql_generator_response: Text2SqlGeneratorResponse | None
    database_query_results: list[str] | None
    sql_query_correction: bool = False
    fallback_response: FallbackResponse | None
    banksql_queries: list[str] | None
    input_request: UIUserRequest | None
    agent_metrics: dict[str, Metrics]  | None
