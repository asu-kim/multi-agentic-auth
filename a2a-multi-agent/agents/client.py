import argparse
import asyncio
import json
import sys
from uuid import uuid4

import httpx

from agents.common.settings import Settings
from agents.common.negotiation_models import SearchRequest, SearchResult
from agents.common.a2a_client import A2APeer


def print_result(result: SearchResult, *, as_json: bool = False, trace: bool = False):
    if as_json:
        print(json.dumps(result.model_dump(), indent=2))
        return
    print(result.summary)
    if trace:
        for action in result.manager_actions:
            source = " (system)" if action.get("summary_source") != "model" else ""
            print(f"  Turn {action['turn']} [{action['tool']}]{source}: "
                  f"{action.get('decision_summary', 'No brief decision summary was provided.')}")
            if action.get("result", {}).get("error"):
                print(f"    Error: {action['result']['error']}")
    for attempt in result.attempts:
        print(f"  {attempt.agent_name} / {attempt.phase}: {attempt.status} — {attempt.detail}")


async def run(args):
    settings = Settings.from_env()
    async with httpx.AsyncClient(timeout=settings.a2a_timeout, trust_env=False) as http:
        peer = A2APeer(http)
        card = await peer.discover(args.url or settings.urls["manager"])
        request = SearchRequest(query=args.query, required_capabilities=args.capability)
        result = SearchResult.model_validate(await peer.send(card, request.model_dump(), uuid4().hex))
        print_result(result, as_json=args.json, trace=args.trace)
        return 0 if result.status == "found" else 1


def main():
    parser = argparse.ArgumentParser(description="Ask the manager to find a capable agent")
    parser.add_argument("query")
    parser.add_argument("--capability", action="append", default=[],
                        help="Exact required skill ID; repeat for multiple requirements. The manager still chooses its actions.")
    parser.add_argument("--url", help="Manager's A2A base URL")
    parser.add_argument("--json", action="store_true", help="Print full result and negotiation history")
    parser.add_argument("--trace", action="store_true", help="Print brief manager decision summaries after the result")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(run(args)))
    except (httpx.HTTPError, ValueError) as exc:
        parser.exit(2, f"Request failed: {exc}\n")


if __name__ == "__main__":
    main()
