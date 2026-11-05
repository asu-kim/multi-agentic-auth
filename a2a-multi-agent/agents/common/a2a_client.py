from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from agents.common.latency import current_trace, capture_dispatch_timestamp
from a2a.types import AgentCard, DataPart, Message, Part, Task, TextPart


def data_message(data: dict, *, role: str = "user", context_id: str | None = None,
                 text: str | None = None) -> Message:
    parts = [Part(root=DataPart(data=data))]
    if text:
        parts.append(Part(root=TextPart(text=text)))
    return Message(role=role, message_id=uuid4().hex, context_id=context_id, parts=parts)


def read_data(message: Message) -> dict | None:
    for part in message.parts:
        if isinstance(part.root, DataPart):
            return part.root.data
    return None


class A2APeer:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http
        self.factory = ClientFactory(ClientConfig(httpx_client=http, streaming=False))

    async def discover(self, url: str) -> AgentCard:
        return await A2ACardResolver(httpx_client=self.http, base_url=url).get_agent_card()

    async def send(self, card: AgentCard, data: dict, context_id: str) -> dict:
        client = self.factory.create(card)
        message = data_message(dict(data), context_id=context_id)
        trace = current_trace()
        if trace is not None and trace.role == "manager" and data.get("protocol") == "agent-task/v1":
            # Stamp after preparing the message, immediately before the SDK send.
            message.parts[0].root.data["dispatch_timing"] = capture_dispatch_timestamp()
        async for event in client.send_message(message):
            if isinstance(event, Message):
                result = read_data(event)
                if result is not None:
                    return result
            elif isinstance(event, tuple) and isinstance(event[0], Task):
                task = event[0]
                for artifact in task.artifacts or []:
                    for part in artifact.parts:
                        if isinstance(part.root, DataPart):
                            return part.root.data
                if task.status.message:
                    result = read_data(task.status.message)
                    if result is not None:
                        return result
        raise ValueError(f"{card.name} returned no structured result")
