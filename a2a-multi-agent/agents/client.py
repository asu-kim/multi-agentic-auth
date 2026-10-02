import argparse
import asyncio
import json
import sys
from uuid import uuid4

import httpx

from agents.common.settings import Settings
from agents.common.negotiation_models import SearchRequest, SearchResult
from agents.common.a2a_client import A2APeer


def print_result(result: SearchResult, *, as_json: bool = False, trace: bool = False, thoughts: bool = False):
    if as_json:
        output = result.model_dump()
        # Hide transport metadata in CLI output only; retain full cards for inference.
        for action in output["manager_actions"]:
            card = action.get("result", {}).get("agent_card")
            if isinstance(card, dict):
                for field in ("capabilities", "defaultInputModes", "defaultOutputModes"):
                    card.pop(field, None)
        # Preserve provider text verbatim and make absent reasoning explicit.
        for turn in output["manager_turns"]:
            fields = turn.get("inference_thoughts", {})
            turn["reasoning_status"] = "returned" if any(fields.values()) else "not_returned"
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return
    print(result.summary)
    if trace:
        for action in result.manager_actions:
            print(f"  Turn {action['turn']} [{action['tool']}]: "
                  f"{json.dumps(action['arguments'])}")
            if action.get("result", {}).get("error"):
                print(f"    Error: {action['result']['error']}")
    if thoughts:
        for turn in result.manager_turns:
            fields = turn["inference_thoughts"]
            if not any(fields.values()):
                print(f"Turn {turn['turn']}: No reasoning text returned by Ollama.")
            else:
                for field, text in fields.items():
                    print(f"Turn {turn['turn']} [Ollama {field}]:")
                    print(text, end="" if text.endswith("\n") else "\n")
    for attempt in result.attempts:
        print(f"  {attempt.agent_name} / {attempt.phase}: {attempt.status} — {attempt.detail}")


async def run(args):
    settings = Settings.from_env()
    async with httpx.AsyncClient(timeout=settings.a2a_timeout, trust_env=False) as http:
        peer = A2APeer(http)
        card = await peer.discover(args.url or settings.urls["manager"])
        request = SearchRequest(query=args.query, required_capabilities=args.capability)
        result = SearchResult.model_validate(await peer.send(card, request.model_dump(), uuid4().hex))
        print_result(result, as_json=args.json, trace=args.trace, thoughts=args.thoughts)
        return 0 if result.status == "found" else 1


def main():
    parser = argparse.ArgumentParser(description="Ask the manager to find a capable agent")
    parser.add_argument("query")
    parser.add_argument("--capability", action="append", default=[],
                        help="Exact required skill ID; repeat for multiple requirements. The manager still chooses its actions.")
    parser.add_argument("--url", help="Manager's A2A base URL")
    parser.add_argument("--json", action="store_true", help="Print result, negotiation history and provider reasoning; omit card transport metadata")
    parser.add_argument("--trace", action="store_true", help="Print manager tool actions after the result")
    parser.add_argument("--thoughts", action="store_true", help="Print unmodified reasoning text returned by Ollama for each manager turn")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(run(args)))
    except (httpx.HTTPError, ValueError) as exc:
        parser.exit(2, f"Request failed: {exc}\n")


if __name__ == "__main__":
    main()
