import logging
from uuid import uuid4

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import InvalidParamsError, UnsupportedOperationError
from a2a.utils.errors import ServerError
from pydantic import ValidationError

from agents.common.a2a_client import data_message, read_data
from agents.common.negotiation_models import SearchRequest, SearchResult
from agents.manager_agent.manager_agent import ManagerAgent

logger = logging.getLogger(__name__)


class ManagerAgentExecutor(AgentExecutor):
    """Receive user requests over A2A and run the manager's discovery graph."""

    def __init__(self, agent: ManagerAgent):
        self.agent = agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.message is None:
            raise ServerError(InvalidParamsError(message="A message is required"))
        data = read_data(context.message)
        try:
            request = SearchRequest.model_validate(data if data is not None else {"query": context.get_user_input()})
        except ValidationError as exc:
            raise ServerError(InvalidParamsError(message=f"Invalid request: {exc}")) from exc
        try:
            result = await self.agent.search(request)
        except Exception as exc:
            logger.exception("Manager search failed")
            result = SearchResult(status="error", query=request.query,
                                  required_capabilities=request.required_capabilities,
                                  summary=f"Search failed: {exc}")
        await event_queue.enqueue_event(data_message(result.model_dump(), role="agent",
            context_id=context.context_id or uuid4().hex, text=result.summary))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(UnsupportedOperationError(message="This demo uses synchronous A2A messages."))
