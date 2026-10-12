"""Send an explicit task to a worker; the manager is not involved."""
import argparse
import asyncio
import json
import sys
from uuid import uuid4
import httpx
from agents.common.settings import Settings
from agents.common.a2a_client import A2APeer
from agents.common.task_models import TaskRequest, TaskResult


async def run(args):
    settings = Settings.from_env()
    async with httpx.AsyncClient(timeout=settings.a2a_timeout, trust_env=False) as http:
        peer = A2APeer(http)
        card = await peer.discover(args.url or settings.urls[args.agent])
        result = TaskResult.model_validate(await peer.send(card,
            TaskRequest(query=args.query).model_dump(), uuid4().hex))
    print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False) if args.json
          else f"{result.agent_name} [{result.status}]\n{result.output}")
    return 0 if result.status in {"completed", "simulated"} else 1


def main():
    parser = argparse.ArgumentParser(description="Send an explicit LLM task to a worker over A2A")
    parser.add_argument("query")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--agent", choices=["language", "analytics", "robot"])
    target.add_argument("--url", help="Worker A2A URL, for example selected_agent.url")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(run(args)))
    except (httpx.HTTPError, ValueError) as exc:
        parser.exit(2, f"Task request failed: {exc}\n")


if __name__ == "__main__":
    main()
