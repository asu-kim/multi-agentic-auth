import json
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class ModelError(RuntimeError):
    pass


class OllamaJSON:
    """Small OpenAI-compatible client; each agent chooses its own model."""

    def __init__(self, http: httpx.AsyncClient, base_url: str, model: str, api_key: str, timeout: float):
        self.http, self.base_url, self.model = http, base_url.rstrip("/"), model
        self.api_key, self.timeout = api_key, timeout

    async def generate(self, system: str, payload: dict, schema: type[T]) -> T:
        messages = [
            {"role": "system", "content": system + " Return only JSON matching the supplied schema."},
            {"role": "user", "content": json.dumps(payload)},
        ]
        # A bounded retry repairs malformed model output, never transport/model errors.
        for attempt in range(2):
            try:
                response = await self.http.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=self.timeout,
                    json={"model": self.model, "messages": messages, "temperature": 0,
                          "stream": False,
                          "response_format": {"type": "json_schema", "json_schema": {
                              "name": schema.__name__, "strict": True, "schema": schema.model_json_schema(),
                          }}},
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                # Include the upstream reason so missing models/URLs don't become opaque internal errors.
                raise ModelError(f"Model {self.model!r} at {self.base_url}: HTTP "
                                 f"{exc.response.status_code}: {exc.response.text[:600]}") from exc
            except httpx.RequestError as exc:
                raise ModelError(f"Cannot reach model {self.model!r} at {self.base_url}: {exc}") from exc
            try:
                content = response.json()["choices"][0]["message"]["content"]
                return schema.model_validate_json(content)
            except (ValueError, TypeError, KeyError, IndexError, ValidationError) as exc:
                if attempt:
                    raise ModelError(f"Model {self.model!r} did not return valid {schema.__name__} JSON") from exc
                messages.append({"role": "user", "content": "Your last response was invalid. Return a JSON object matching the schema exactly."})
        raise AssertionError("Unreachable")
