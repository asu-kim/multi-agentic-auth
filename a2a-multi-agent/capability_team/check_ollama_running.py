import asyncio
import sys

import httpx

from capability_team.common.settings import Settings


async def main():
    settings = Settings.from_env()
    print(f"Checking Ollama: {settings.llm_url}")
    async with httpx.AsyncClient(timeout=15, trust_env=False) as http:
        response = await http.get(f"{settings.llm_url}/models")
        response.raise_for_status()
        available = {entry["id"] for entry in response.json()["data"]}
    missing = {settings.manager_model, settings.subagent_model} - available
    for name in sorted({settings.manager_model, settings.subagent_model}):
        print(f"  {name}: {'MISSING' if name in missing else 'available'}")
    if missing:
        print("Pull missing models using the same OLLAMA_HOST as the server above.")
    return 1 if missing else 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:
        sys.exit(f"Ollama check failed: {exc}")
