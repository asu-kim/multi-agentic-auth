import logging
from uuid import uuid4

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import InvalidParamsError, UnsupportedOperationError
from a2a.utils.errors import ServerError
from pydantic import ValidationError

from capability_team.common.a2a_client import data_message, read_data
from capability_team.common.negotiating_agent import NegotiatingAgent
from capability_team.common.negotiation_models import NegotiationRequest

logger = logging.getLogger(__name__)


class NegotiationExecutor(AgentExecutor):
    """Common A2A handling for sub-agent proposals and confirmations."""

    def __init__(self, agent: NegotiatingAgent):
        self.agent = agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.message is None:
            raise ServerError(InvalidParamsError(message="A message is required"))
        try:
            request = NegotiationRequest.model_validate(read_data(context.message))
        except ValidationError as exc:
            raise ServerError(InvalidParamsError(message=f"Invalid request: {exc}")) from exc
        context_id = context.context_id or uuid4().hex
        try:
            result = await self.agent.negotiate(request, context_id)
        except Exception as exc:
            logger.exception("%s negotiation failed", self.agent.card.name)
            result = self.agent.reply(request, "error", f"Negotiation failed: {exc}")
        await event_queue.enqueue_event(data_message(result.model_dump(), role="agent",
                                                      context_id=context_id, text=result.reason))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(UnsupportedOperationError(message="This demo uses synchronous A2A messages."))
