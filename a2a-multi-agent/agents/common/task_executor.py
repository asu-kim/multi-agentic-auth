import logging
from uuid import uuid4

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import InvalidParamsError, UnsupportedOperationError
from a2a.utils.errors import ServerError
from pydantic import ValidationError

from agents.common.a2a_client import data_message, read_data
from agents.common.task_models import TaskRequest, TaskResult

logger = logging.getLogger(__name__)


class TaskExecutor(AgentExecutor):
    def __init__(self, agent):
        self.agent = agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.message is None:
            raise ServerError(InvalidParamsError(message="A message is required"))
        data = read_data(context.message)
        try:
            request = TaskRequest.model_validate(data if data is not None else {"query": context.get_user_input()})
        except ValidationError as exc:
            raise ServerError(InvalidParamsError(message=f"Invalid task request: {exc}")) from exc
        try:
            result = await self.agent.execute_task(request)
        except Exception as exc:
            logger.exception("%s task failed", self.agent.card.name)
            result = TaskResult(agent_name=self.agent.card.name, status="error", output=f"Task failed: {exc}")
        await event_queue.enqueue_event(data_message(result.model_dump(), role="agent",
            context_id=context.context_id or uuid4().hex, text=result.output))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(UnsupportedOperationError(message="This demo uses synchronous task requests."))
