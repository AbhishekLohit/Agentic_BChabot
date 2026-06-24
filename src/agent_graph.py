# ...existing code...
from langgraph.graph import StateGraph, START, END
from app_models import Graph_State
from typing import Any
from agents.context_builder import context_builder_agent
from agents.supervisor import suprivisor_agent
from agents.text2sql_generator import text2sql_generator_agent    
from agents.sql_execution import execute_query  
from agents.response_generator import response_generator_agent
from agents.fallback import fallback_agent
from agents.visualization_agent import visualization_agent
from agents.sql_correction import sql_correction
from agents.guardrails import input_guardrail_node
from agents.help_center_agent import help_center_agent
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
                 help_store: Any = None
                 ):
        self.llm = openai_llm
        self.prompts = prompts
        self.agent_subgraph = agent_subgraph
        self.help_store = help_store
        self.app = self._workflow()
        self.logger = logger

    def _workflow(self):
        builder = StateGraph(Graph_State)

        builder.add_node("guardrail", self._guardrail)
        builder.add_node("context_builder", self._context_builder)
        builder.add_node("suprivisor", self._suprivisor_agent)
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
                "context_builder": "context_builder",
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
                "sql_generation": "sql_generation",
                "help_center": "help_center",
                "fallback": "fallback",
                END: END,
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

    async def _guardrail(self, state: Graph_State) -> Graph_State:
        await input_guardrail_node(state, self.llm, self.logger)
        return state

    def _guardrail_router(self, state: Graph_State) -> str:
        if state.fallback_response and state.fallback_response.fallback_flag:
            return "fallback"  # Jump straight to fallback response generation
        return "context_builder"  # Proceed with normal conversational extraction

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
        # self.logger.info(f"Context builder response: {state.context_builder_response}")
        return state

    def _supervisor_router(self, state: Graph_State) -> list[str]:
        routes = []

        banksql = state.banksql_queries or []
        help_center = state.help_center_queries or []
        out_of_scope = state.out_of_scope_queries or []

        # One or more valid (BankSQL) intents → hand them ALL to the SQL agent. For a
        # single intent it just generates the query; for multiple intents the SQL agent
        # decides whether they can be answered by ONE combined query (else it falls back
        # asking the user to ask them separately).
        # Pick a SINGLE primary route. When a message mixes BankSQL and help intents,
        # go to sql_generation first; `_after_sql_router` then chains into help_center.
        if len(banksql) >= 1:
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
                              self.logger
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
