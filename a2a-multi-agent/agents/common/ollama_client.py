import json
from typing import TypeVar
from uuid import uuid4

import httpx
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ModelError(RuntimeError):
    pass


class OllamaJSON:
    """Ollama client: structured sub-agent assessments and native manager tool calls."""

    def __init__(self, http: httpx.AsyncClient, base_url: str, model: str, api_key: str, timeout: float):
        self.http, self.base_url, self.model = http, base_url.rstrip("/"), model
        self.api_key, self.timeout = api_key, timeout

    async def _completion(self, messages: list[dict], **options) -> dict:
        try:
            response = await self.http.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"}, timeout=self.timeout,
                json={"model": self.model, "messages": messages, "temperature": 0,
                      "stream": False, **options},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ModelError(f"Model {self.model!r} at {self.base_url}: HTTP "
                             f"{exc.response.status_code}: {exc.response.text[:600]}") from exc
        except httpx.RequestError as exc:
            raise ModelError(f"Cannot reach model {self.model!r} at {self.base_url}: {exc}") from exc
        try:
            message = response.json()["choices"][0]["message"]
            if not isinstance(message, dict):
                raise ValueError("message is not an object")
            return message
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise ModelError(f"Model {self.model!r} returned an invalid chat completion") from exc

    async def generate(self, system: str, payload: dict, schema: type[T]) -> T:
        messages = [
            {"role": "system", "content": system + " Return only JSON matching the supplied schema."},
            {"role": "user", "content": json.dumps(payload)},
        ]
        for attempt in range(2):
            message = await self._completion(messages, response_format={
                "type": "json_schema", "json_schema": {
                    "name": schema.__name__, "strict": True, "schema": schema.model_json_schema(),
                },
            })
            try:
                return schema.model_validate_json(message.get("content"))
            except (ValueError, TypeError) as exc:
                if attempt:
                    raise ModelError(f"Model {self.model!r} did not return valid {schema.__name__} JSON") from exc
                messages.append({"role": "user", "content": "Your last response was invalid. Return a JSON object matching the schema exactly."})
        raise AssertionError("Unreachable")

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        """Let the manager model select tools; preserve tool IDs for the next turn."""
        message = await self._completion(messages, tools=tools, tool_choice="auto", parallel_tool_calls=False)
        # Only replay protocol fields, not model-specific private reasoning fields.
        assistant = {"role": "assistant", "content": message.get("content") or ""}
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list) or len(calls) > 8:
            raise ModelError("Expected at most eight tool calls in a model response")
        if calls:
            normalized, ids = [], set()
            for call in calls:
                try:
                    function = call["function"]
                    name, arguments = function["name"], function["arguments"]
                    if not isinstance(name, str) or not name:
                        raise ValueError("Missing function name")
                    if isinstance(arguments, dict):
                        arguments = json.dumps(arguments)
                    if not isinstance(arguments, str):
                        raise ValueError("Invalid function arguments")
                    call_id = call.get("id") or uuid4().hex
                    if not isinstance(call_id, str) or call_id in ids:
                        raise ValueError("Invalid or duplicate tool-call ID")
                    ids.add(call_id)
                    normalized.append({"id": call_id, "type": "function",
                                       "function": {"name": name, "arguments": arguments}})
                except (KeyError, TypeError, ValueError) as exc:
                    raise ModelError("Model returned a malformed tool call") from exc
            assistant["tool_calls"] = normalized
        return assistant
