import logging
from typing import Any
import httpx, hmac, hashlib, secrets, binascii
from uuid import uuid4

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import (
    InternalError,
    InvalidParamsError,
    Part,
    TaskState,
    TextPart,
    AgentCard,
    MessageSendParams,
    UnsupportedOperationError,
    SendMessageRequest,
    SendMessageSuccessResponse,
)
from a2a.utils import (
    new_agent_text_message,
    new_task,
)
from a2a.utils.errors import ServerError
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from app.agent import ManagerAgent

AGENT2_BASE_URL = "http://localhost:9998"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _extract_first_text_from_send_message_response(resp: Any) -> str:
    root = getattr(resp, "root", resp)

    if isinstance(root, SendMessageSuccessResponse):
        message = root.result
        for part in message.parts:
            if hasattr(part, "root") and isinstance(part.root, TextPart):
                return part.root.text
        return "(Agent1 responded but no TextPart found)"
    else:
        try:
            return f"(Non-success response) {root.model_dump(mode='json', exclude_none=True)}"
        except Exception:
            return f"(Non-success response) {str(root)}"


def _extract_task_text_from_context(context: RequestContext) -> str:
    if not context.message or not context.message.parts:
        return ""

    for part in context.message.parts:
        if hasattr(part, "text") and isinstance(part, TextPart):
            return part.text

        if hasattr(part, "root") and isinstance(part.root, TextPart):
            return part.root.text

    return ""


async def agent1_call_agent2(httpx_client: httpx.AsyncClient, agent2_card: AgentCard, user_text: str) -> str:
    client = A2AClient(httpx_client=httpx_client, agent_card=agent2_card)

    send_message_payload: dict[str, Any] = {
        "message": {
            "role": "user",
            "parts": [{"kind": "text", "text": user_text}],
            "messageId": uuid4().hex,
        }
    }
    request = SendMessageRequest(id=str(uuid4()), params=MessageSendParams(**send_message_payload))
    response = await client.send_message(request)
    return _extract_first_text_from_send_message_response(response)


async def _fetch_agent2_card(httpx_client: httpx.AsyncClient) -> AgentCard:
    resolver = A2ACardResolver(httpx_client=httpx_client, base_url=AGENT2_BASE_URL)
    logger.info(f"[Agent2] Fetch public card: {AGENT2_BASE_URL}{AGENT_CARD_WELL_KNOWN_PATH}")
    public_card = await resolver.get_agent_card()
    return public_card


class ManagerAgentExecutor(AgentExecutor):
    """Currency Conversion AgentExecutor Example."""

    def __init__(self):
        self.agent = ManagerAgent()

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        error = self._validate_request(context)
        if error:
            raise ServerError(error=InvalidParamsError())

        query = context.get_user_input()
        task = context.current_task
        if not task:
            task = new_task(context.message)  # type: ignore
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        try:
            async for item in self.agent.stream(query, task.context_id):
                is_task_complete = item['is_task_complete']
                require_user_input = item['require_user_input']

                if not is_task_complete and not require_user_input:
                    await updater.update_status(
                        TaskState.working,
                        new_agent_text_message(
                            item['content'],
                            task.context_id,
                            task.id,
                        ),
                    )
                elif require_user_input:
                    await updater.update_status(
                        TaskState.input_required,
                        new_agent_text_message(
                            item['content'],
                            task.context_id,
                            task.id,
                        ),
                        final=True,
                    )
                    break
                else:
                    await updater.add_artifact(
                        [Part(root=TextPart(text=item['content']))],
                        name='conversion_result',
                    )
                    await updater.complete()
                    break

        except Exception as e:
            logger.error(f'An error occurred while streaming the response: {e}')
            raise ServerError(error=InternalError()) from e

    def _validate_request(self, context: RequestContext) -> bool:
        return False

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise ServerError(error=UnsupportedOperationError())
