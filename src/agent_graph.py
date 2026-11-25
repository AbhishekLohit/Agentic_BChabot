from langgraph.graph import StateGraph, START, END
from app_models import DisputeResult, FallbackResponse, Graph_State
from typing import Any
from agents.context_builder import context_builder_agent
from agents.supervisor import suprivisor_agent
from agents.response_generator import response_generator_agent
from agents.fallback import fallback_agent
from agents.visualization_agent import visualization_agent
from agents.guardrails import input_guardrail_node
from agents.help_center_agent import help_center_agent
from agents.disputes import checks as dispute_checks
from agents.disputes import workflow as dispute_workflow
from agents.disputes.registry import DisputeRegistry
from typing import Any as LLMType
from app_models import Prompts
from logging import Logger
from agent_subraph import Agent_SubGraph

class Agent_Graph:
    def __init__(self,
                 openai_llm:LLMType,
                 prompts:Prompts,
                 logger: Logger,
                 agent_subgraph: Agent_SubGraph,
                 help_store: Any = None,
                 db_conn: Any = None,
                 settings: Any = None,
                 dispute_registry: DisputeRegistry | None = None,
                 ):
        self.llm = openai_llm
        self.prompts = prompts
        self.agent_subgraph = agent_subgraph
        self.help_store = help_store
        # Disputes need all three; without them the graph behaves as it did before.
        self.db_conn = db_conn
        self.settings = settings
        self.dispute_registry = dispute_registry if (db_conn is not None and settings is not None) else None
        self.app = self._workflow()
        self.logger = logger

    def _workflow(self):
        builder = StateGraph(Graph_State)

        builder.add_node("guardrail", self._guardrail)
        builder.add_node("dispute_gate", self._dispute_gate)
        builder.add_node("context_builder", self._context_builder)
        builder.add_node("suprivisor", self._suprivisor_agent)
        builder.add_node("dispute_agent", self._dispute_agent_node)
        builder.add_node("dispute_list", self._dispute_list_node)
        builder.add_node("sql_generation", self.agent_subgraph.app)
        builder.add_node("help_center", self._help_center_agent_node)
        builder.add_node("response_generator", self._response_generator_agent)
        builder.add_node("visualization_agent", self._visualization_agent_node)
        builder.add_node("fallback", self._fallback)
        builder.add_node("join", self._join)

        # FLOW---------------------------------
        builder.add_edge(START, "guardrail")
        builder.add_conditional_edges(
            "guardrail",
            self._guardrail_router,
            {
                "fallback": "fallback",
                "dispute_gate": "dispute_gate",
                "dispute_list": "dispute_list",
            }
        )
        # "Show my disputes": straight from the database, then the normal reply path.
        builder.add_edge("dispute_list", "join")
        # The gate: while a dispute is open, decide whether this message belongs to it.
        builder.add_conditional_edges(
            "dispute_gate",
            self._dispute_gate_router,
            {
                "context_builder": "context_builder",
                "dispute_agent": "dispute_agent",
                "fallback": "fallback",
            }
        )
        builder.add_conditional_edges(
            "context_builder",
            self._context_builder_router,
            {
                "suprivisor": "suprivisor",
                "fallback": "fallback",
                END: END,
            }
        )
        builder.add_conditional_edges(
            "suprivisor",
            self._supervisor_router,
            {
                "dispute_agent": "dispute_agent",
                "sql_generation": "sql_generation",
                "help_center": "help_center",
                "fallback": "fallback",
                END: END,
            }
        )
        # After the Disputes agent, carry on with whatever else the same message asked for
        # (sequentially, like the SQL -> help chain below, so no two branches write the
        # shared agent_metrics channel concurrently). The agent runs at most once per
        # request (`dispute_handled`), so the cycle through context_builder cannot loop.
        builder.add_conditional_edges(
            "dispute_agent",
            self._after_dispute_router,
            {
                "context_builder": "context_builder",
                "sql_generation": "sql_generation",
                "help_center": "help_center",
                "join": "join",
            }
        )
        # BankSQL pipeline. A message can mix a BankSQL query AND a help query; in that
        # case the supervisor routes to sql_generation first, then this conditional edge
        # chains into help_center afterwards (sequential, so the branches never write the
        # shared agent_metrics channel concurrently).
        builder.add_conditional_edges(
            "sql_generation",
            self._after_sql_router,
            {
                "help_center": "help_center",
                "join": "join",
            }
        )
        builder.add_edge("help_center", "join")
        builder.add_edge("fallback", "join")
        builder.add_edge("join", "visualization_agent")
        builder.add_edge("visualization_agent", "response_generator")
        builder.add_edge("response_generator", END)

        app = builder.compile()
        return app

    # ------------------------------------------------------------------ guardrail
    def _open_draft(self, state: Graph_State):
        """The dispute in progress for this session, if there is one."""
        if self.dispute_registry is None:
            return None
        request = state.input_request
        try:
            return dispute_workflow.load_open_draft(
                self.db_conn, request.customer_id, request.session_id,
                self.settings.DISPUTE_DRAFT_MAX_AGE_HOURS,
            )
        except Exception:
            self.logger.exception("Could not read the open dispute draft")
            return None

    async def _guardrail(self, state: Graph_State) -> Graph_State:
        # "Show my disputes" / a case ID: answered from the database by code alone. The message
        # never reaches a model on that path, so there is nothing for the guardrail to protect,
        # and its per-message reading wrongly blocked these requests as "sensitive data".
        if dispute_workflow.is_case_list_request(state.input_request.user_query or ""):
            if state.agent_metrics is None:
                state.agent_metrics = {}
            state.dispute_list_request = True
            self.logger.info("Dispute list request: answering from the database, no model calls.")
            return state
        await input_guardrail_node(state, self.llm, self.logger)
        fallback = state.fallback_response
        if fallback and fallback.fallback_reason == "greeting" and self._open_draft(state):
            # The guardrail reads each message on its own, so a bare "yes" or "ok" looks like a
            # pleasantry. While a dispute is waiting on the customer it is an answer.
            self.logger.info("Ignoring a greeting verdict: a dispute is waiting on the customer.")
            state.fallback_response = None
        return state

    def _guardrail_router(self, state: Graph_State) -> str:
        if state.dispute_list_request:
            return "dispute_list"
        if state.fallback_response and state.fallback_response.fallback_flag:
            return "fallback"  # Jump straight to fallback response generation
        return "dispute_gate"  # Check for an open dispute, then normal conversational extraction

    # ------------------------------------------------------------------ disputes
    async def _dispute_gate(self, state: Graph_State) -> Graph_State:
        """Runs on every request, after the guardrail. With no dispute open it does nothing.
        With one open, a single model call reads the message and says whether it is a reply
        to the dispute, an unrelated question, both, or a request to dispute something else."""
        state.dispute_open = False
        draft = self._open_draft(state)
        if draft is None:
            return state

        request = state.input_request
        try:
            if state.agent_metrics is None:
                state.agent_metrics = {}
            ext = await dispute_workflow.extract_for_open_draft(
                llm=self.llm,
                prompt=self.prompts.dispute_extraction_prompt,
                registry=self.dispute_registry,
                db_conn=self.db_conn,
                logger=self.logger,
                customer_id=request.customer_id,
                draft=draft,
                user_message=request.user_query,
                agent_metrics=state.agent_metrics,
            )
            self.logger.info(f"Dispute gate: message_kind={ext.message_kind}")
            state.dispute_open = True
            state.dispute_gate_kind = ext.message_kind
            state.dispute_extraction = ext

            if ext.message_kind == "side_question":
                txn = (dispute_checks.get_transaction(self.db_conn, request.customer_id, draft.transaction_id)
                       if draft.transaction_id else None)
                proposed = (
                    dispute_checks.get_transaction(self.db_conn, request.customer_id, draft.proposed_transaction_id)
                    if draft.proposed_transaction_id else None
                )
                state.dispute_reminder = dispute_workflow.pending_reminder(
                    draft, txn, self.dispute_registry, proposed
                )
            elif ext.message_kind == "both":
                state.pipeline_query = ext.side_question_text or None
            elif ext.message_kind == "new_dispute":
                dispute_workflow.abandon_draft(
                    self.db_conn, request.customer_id, request.session_id, draft, "customer started another dispute"
                )
                state.dispute_open = False
                state.dispute_extraction = None
        except Exception as e:
            self.logger.exception("Error in dispute gate")
            state.fallback_response = FallbackResponse(
                fallback_flag=True,
                fallback_node="dispute_gate",
                fallback_reason="exception",
                error_detail=f"{type(e).__name__}: {e}",
                fallback_message="I'm sorry, but I'm currently unable to process your request due to a technical issue. Please try again later."
            )
        return state

    def _dispute_gate_router(self, state: Graph_State) -> str:
        if state.fallback_response and state.fallback_response.fallback_flag:
            return "fallback"
        ext = state.dispute_extraction
        if not state.dispute_open or ext is None:
            return "context_builder"
        if ext.message_kind == "side_question":
            return "context_builder"   # answer it normally; the reply reminds them about the dispute
        return "dispute_agent"          # a reply to the dispute (possibly with a side question too)

    async def _dispute_list_node(self, state: Graph_State) -> Graph_State:
        """The customer's own disputes, read from the database with a fixed query. No model call:
        the reply is written by code (workflow._case_list_reply) from these rows."""
        request = state.input_request
        state.dispute_handled = True
        try:
            state.dispute_result = dispute_workflow.case_list_for(
                self.db_conn, request.customer_id, request.session_id, request.user_query, self.settings,
            )
        except Exception as e:
            self.logger.exception("Error listing disputes")
            state.dispute_result = DisputeResult(
                status="error", summary="The dispute list could not be read.",
                data={"error": f"{type(e).__name__}: {e}"},
            )
        return state

    async def _dispute_agent_node(self, state: Graph_State) -> Graph_State:
        """The Disputes agent: one turn of finding the payment, confirming the reason, asking
        that reason's questions, and (after an explicit yes) raising the case."""
        state.dispute_handled = True
        if state.agent_metrics is None:
            state.agent_metrics = {}
        request = state.input_request
        ext = state.dispute_extraction   # set when the gate already read this message

        # Always the customer's own words, never the context builder's rewrite. The context builder
        # fills gaps from earlier conversation (even other sessions), which would let an amount or
        # date the customer never typed reach the search. The amount and date must come from them.
        message = request.user_query

        state.dispute_result = await dispute_workflow.run_dispute_turn(
            llm=self.llm,
            prompt=self.prompts.dispute_extraction_prompt,
            registry=self.dispute_registry,
            db_conn=self.db_conn,
            logger=self.logger,
            customer_id=request.customer_id,
            session_id=request.session_id,
            user_message=message,
            settings=self.settings,
            agent_metrics=state.agent_metrics,
            extraction=ext,
            # a "yes" only counts when it stands alone, not alongside another request
            allow_confirm=not (ext is not None and ext.message_kind == "both"),
        )
        state.dispute_extraction = None
        return state

    def _after_dispute_router(self, state: Graph_State) -> str:
        """After the Disputes agent, carry on with the rest of the same message."""
        if state.pipeline_query:
            return "context_builder"     # the unrelated part of a message that also replied to the dispute
        if state.banksql_queries:
            return "sql_generation"
        if state.help_center_queries:
            return "help_center"
        return "join"

    # ------------------------------------------------------------------ pipeline
    async def _context_builder(self, state: Graph_State) -> Graph_State:
        """
        Context builder node: placeholder that ensures the context_builder_response
        field exists on the Graph_State and sets it to None for now.
        Replace with real context-building logic when ready.
        """
        await context_builder_agent(state,
                              self.llm,
                              self.prompts.context_builder_prompt,
                              self.logger
        )
        # self.logger.info(f"Context builder response: {state.context_builder_response}")
        return state

    async def _suprivisor_agent(self, state: Graph_State) -> Graph_State:
        """
        Supervisor node: placeholder that ensures the suprivisor_response
        field exists on the Graph_State and sets it to None for now.
        """
        await suprivisor_agent(state,
                        self.llm,
                        self.prompts.suprivisor_prompt,
                        self.logger
                        )
        # Disputes that cannot be handled (not configured, or the agent already ran for this
        # message) are treated like any other request we cannot help with.
        if state.dispute_queries and (self.dispute_registry is None or state.dispute_handled):
            state.out_of_scope_queries = [*(state.out_of_scope_queries or []), *state.dispute_queries]
            state.dispute_queries = []
        # self.logger.info(f"Context builder response: {state.context_builder_response}")
        return state

    def _supervisor_router(self, state: Graph_State) -> list[str]:
        routes = []

        banksql = state.banksql_queries or []
        help_center = state.help_center_queries or []
        dispute = state.dispute_queries or []
        out_of_scope = state.out_of_scope_queries or []

        # A customer raising a dispute → the Disputes agent first. Anything else in the same
        # message is picked up afterwards by `_after_dispute_router`.
        if len(dispute) >= 1:
            routes.append("dispute_agent")

        # One or more valid (BankSQL) intents → hand them ALL to the SQL agent. For a
        # single intent it just generates the query; for multiple intents the SQL agent
        # decides whether they can be answered by ONE combined query (else it falls back
        # asking the user to ask them separately).
        # Pick a SINGLE primary route. When a message mixes BankSQL and help intents,
        # go to sql_generation first; `_after_sql_router` then chains into help_center.
        elif len(banksql) >= 1:
            routes.append("sql_generation")

        # Help-only message → answer it directly from the Barclays help KB.
        elif len(help_center) >= 1:
            routes.append("help_center")

        # Only out-of-scope intents (nothing actionable at all) → fallback explains
        # we can't help.
        elif len(out_of_scope) > 0:
            routes.append("fallback")

        if not routes:
            routes.append(END)

        return routes

    def _after_sql_router(self, state: Graph_State) -> str:
        """After the SQL branch, chain into the help branch if the message also had
        a help/how-to query; otherwise proceed to the join."""
        if state.help_center_queries:
            return "help_center"
        return "join"

    def _context_builder_router(self, state: Graph_State) -> list[str]:
        routes = []

        if state.fallback_response:
            routes.append("fallback")
        else:
            routes.append("suprivisor")

        return routes


    async def _help_center_agent_node(self, state: Graph_State) -> Graph_State:
        """Help-centre RAG node: retrieves Barclays help passages and answers from them."""
        await help_center_agent(
            state,
            self.llm,
            self.prompts.help_center_prompt,
            self.help_store,
            self.logger,
        )
        return state

    async def _visualization_agent_node(self, state: Graph_State) -> Graph_State:
        await visualization_agent(
            state,
            self.llm,
            self.prompts.visualization_prompt,
            self.logger,
        )
        return state

    async def _response_generator_agent(self, state: Graph_State) -> Graph_State:
        """
        Response generator node: placeholder that ensures the response_generator_response
        field exists on the Graph_State and sets it to None for now.
        Replace with real response generation logic when ready.
        """
        await response_generator_agent(state,
                              self.llm,
                              self.prompts.responsegenerator_prompt,
                              self.logger,
                              dispute_prompt=self.prompts.dispute_reply_prompt
        )

        return state

    async def _join(self, state: Graph_State) -> Graph_State:
        # Nothing to do — just a synchronization point
        self.logger.info("Joining branches.")
        return state

    async def _fallback(self, state: Graph_State):
        """
        Fallback node: placeholder that ensures the fallback_response
        field exists on the Graph_State and sets it to None for now.
        """
        await fallback_agent(state,
                              self.logger
        )
        self.logger.info(f"Fallback response: {state.fallback_response}")
        return {
            "fallback_response": state.fallback_response
        }
