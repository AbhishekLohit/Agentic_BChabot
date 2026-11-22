from typing import Any, Literal
from pydantic import BaseModel,Field, field_validator
from langchain_core.messages import BaseMessage
from datetime import datetime
from langchain_core.prompts import BasePromptTemplate


class Prompts(BaseModel):
    context_builder_prompt: BasePromptTemplate
    suprivisor_prompt: BasePromptTemplate
    text2sqlgenerator_prompt: BasePromptTemplate
    responsegenerator_prompt: BasePromptTemplate
    sql_correction_prompt: BasePromptTemplate
    visualization_prompt: BasePromptTemplate
    history_summarizer_prompt: BasePromptTemplate
    help_center_prompt: BasePromptTemplate
    dispute_extraction_prompt: BasePromptTemplate
    dispute_reply_prompt: BasePromptTemplate


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
    class_label: Literal["BankSQL","Out_of_scope","HelpCenter","Dispute"]
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
    chart_type: Literal["bar", "line", "pie", "none"]
    chart_title: str
    x_field: str | None
    y_field: str | None
    reasoning: str

class VisualizationResponse(BaseModel):
    decision: VisualizationDecision
    plotly_json: str | None

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

# ---------------------------------------------------------------------------
# Disputes
# ---------------------------------------------------------------------------

DisputeReason = Literal[
    "duplicate_charge",
    "wrong_amount",
    "refund_not_received",
    "goods_not_received",
    "not_as_described",
    "cancelled_subscription",
    "unrecognised",
]


class SlotAnswer(BaseModel):
    slot_id: str
    value: str


class DisputeTurnExtraction(BaseModel):
    """What the model read out of the customer's latest message. Code, not the
    model, decides what happens next."""
    # Only set when the customer clearly stated it (or it is obvious from context).
    reason: DisputeReason | None = None
    answers: list[SlotAnswer] = Field(default_factory=list)
    intent: Literal["provide_info", "confirm", "decline", "cancel", "unclear"] = "provide_info"
    # Only used after we asked "do you recognise this payment now?".
    recognises_charge: bool | None = None
    # Which payment the dispute is about, as the customer gave it: the amount in pounds as a
    # plain number, the date as YYYY-MM-DD and the merchant name. Only what they actually
    # stated: code needs all three before it searches, and asks for whatever is missing.
    transaction_amount: float | None = None
    transaction_date: str | None = None
    merchant_hint: str | None = None
    # When we listed candidate payments: the id of the one they picked. Must be one of the
    # listed ids; code ignores anything else.
    chosen_transaction_id: str | None = None
    # Only meaningful while a dispute is already open: how this message relates to it.
    message_kind: Literal["dispute", "side_question", "both", "new_dispute"] = "dispute"
    # For "both": the unrelated part of the message, rewritten as a standalone request.
    side_question_text: str | None = None


class DisputeDraft(BaseModel):
    """A dispute being put together over several turns. Persisted in SQLite."""
    # The payment the customer has CONFIRMED. Nothing else in the draft is used until it is set.
    transaction_id: str | None = None
    # What the customer has told us about the payment, used to find it in the database.
    payment_amount: float | None = None
    payment_date: str | None = None
    merchant_hint: str | None = None
    # [amount, date] of the last search, so the same search is not repeated when it found nothing.
    last_search: list[Any] | None = None
    # The exact match, shown to the customer and waiting for their yes or no.
    proposed_transaction_id: str | None = None
    # No exact match: the closest payments, listed for the customer to pick one.
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    reason: DisputeReason | None = None
    answers: dict[str, str] = Field(default_factory=dict)
    status: Literal["collecting", "awaiting_confirmation"] = "collecting"
    prechecks_done: bool = False
    precheck_notes: dict[str, Any] = Field(default_factory=dict)
    # unrecognised-charge triage
    recognition_asked: bool = False
    awaiting_recognition: bool = False
    # Slots asked in the previous reply, so a terse answer can be mapped to its question.
    pending_slot_ids: list[str] = Field(default_factory=list)
    # Fingerprint of what the customer was shown at the confirmation step.
    summary_hash: str | None = None


class DisputeResult(BaseModel):
    """What the Disputes agent decided this turn. The response generator turns it into the reply."""
    status: Literal[
        "needs_transaction", "needs_payment_confirmation", "needs_transaction_choice",
        "not_eligible", "needs_reason",
        "needs_info", "needs_confirmation", "resolved", "suspected_fraud", "filed",
        "cancelled", "error",
    ]
    summary: str
    needs_user_input: bool = False
    data: dict[str, Any] = Field(default_factory=dict)


class Graph_State(BaseModel):
    input_request: UIUserRequest | None
    chat_history: list[ChatMessage] |   None
    customer_id: str | None
    context_builder_response: ContextBuilderResponse | None
    suprivisor_response: SuprivisorResponse | None
    text2sql_generator_response: Text2SqlGeneratorResponse | None
    database_query_results: list[dict[str, Any]] | None
    database_results_truncated: bool = False
    sql_query_correction: bool = False
    banksql_queries: list[str] | None
    out_of_scope_queries: list[str] | None
    help_center_queries: list[str] | None = None
    help_center_response: ResponseGeneratorResponse | None = None
    fallback_response: FallbackResponse | None
    response_generation_response: ResponseGeneratorResponse | None
    visualization_response: VisualizationResponse | None = None
    visualization_only: bool = False
    agent_metrics: dict[str, Metrics]  | None
    # --- Disputes -------------------------------------------------------------
    dispute_queries: list[str] | None = None
    # A dispute draft was already open when this request started.
    dispute_open: bool = False
    # The gate's verdict on the message when a dispute was open (None when none was):
    # dispute | side_question | both | new_dispute.
    dispute_gate_kind: str | None = None
    # The Disputes agent has run in this request; a second pass is ignored (no loops).
    dispute_handled: bool = False
    # The gate's reading of the message, handed to the Disputes agent so it is not read twice.
    dispute_extraction: DisputeTurnExtraction | None = None
    dispute_result: DisputeResult | None = None
    # Appended to the reply after a side question, to say the dispute is still open.
    dispute_reminder: str | None = None
    # Replaces the customer's message as the input to the context builder (the unrelated
    # part of a message that also continued a dispute).
    pipeline_query: str | None = None

class SubGraph_State(BaseModel):
    text2sql_generator_response: Text2SqlGeneratorResponse | None
    database_query_results: list[dict[str, Any]] | None
    database_results_truncated: bool = False
    sql_query_correction: bool = False
    fallback_response: FallbackResponse | None
    banksql_queries: list[str] | None
    input_request: UIUserRequest | None
    agent_metrics: dict[str, Metrics]  | None
