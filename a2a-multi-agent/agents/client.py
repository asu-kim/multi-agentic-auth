import argparse
import asyncio
import json
import sys
from uuid import uuid4

import httpx

from agents.common.settings import Settings
from agents.common.negotiation_models import SearchRequest, SearchResult
from agents.common.a2a_client import A2APeer


async def run(args):
    settings = Settings.from_env()
    async with httpx.AsyncClient(timeout=settings.a2a_timeout, trust_env=False) as http:
        peer = A2APeer(http)
        card = await peer.discover(args.url or settings.urls["manager"])
        request = SearchRequest(query=args.query, required_capabilities=args.capability)
        result = SearchResult.model_validate(await peer.send(card, request.model_dump(), uuid4().hex))
        print(json.dumps(result.model_dump(), indent=2) if args.json else result.summary)
        if not args.json:
            for attempt in result.attempts:
                print(f"  {attempt.agent_name} / {attempt.phase}: {attempt.status} — {attempt.detail}")
        return 0 if result.status == "found" else 1


def main():
    parser = argparse.ArgumentParser(description="Ask the manager to find a capable agent")
    parser.add_argument("query")
    parser.add_argument("--capability", action="append", default=[],
                        help="Exact required skill ID; repeat for multiple requirements. Skips LLM extraction.")
    parser.add_argument("--url", help="Manager's A2A base URL")
    parser.add_argument("--json", action="store_true", help="Print full result and negotiation history")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(run(args)))
    except (httpx.HTTPError, ValueError) as exc:
        parser.exit(2, f"Request failed: {exc}\n")


if __name__ == "__main__":
    main()
