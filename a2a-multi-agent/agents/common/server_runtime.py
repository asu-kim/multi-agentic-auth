import argparse
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore

from agents.common.ollama_client import OllamaJSON
from agents.common.settings import Settings

logger = logging.getLogger(__name__)


def create_model(settings: Settings, model: str):
    http = httpx.AsyncClient(timeout=settings.a2a_timeout, trust_env=False)
    llm = OllamaJSON(http, settings.llm_url, model, settings.api_key, settings.llm_timeout)
    return http, llm


def build_a2a_app(card, executor, http=None):
    """Serve the supplied card and executor; no agent identities or skills live here."""
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore())
    app = A2AStarletteApplication(agent_card=card, http_handler=handler).build()

    @asynccontextmanager
    async def lifespan(app):
        logger.info("%s serving A2A at %s", card.name, card.url)
        try:
            yield
        finally:
            if http is not None:
                await http.aclose()

    app.router.lifespan_context = lifespan
    return app


def run_server(role: str, create_app, argv=None):
    parser = argparse.ArgumentParser(description=f"Run the {role} agent's A2A server")
    parser.add_argument("--host", default="127.0.0.1", help="Listen address; advertised URL comes from .env")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    port = urlsplit(settings.urls[role]).port
    if port is None:
        parser.error(f"{role.upper()}_URL must include a port")
    model = settings.manager_model if role == "manager" else settings.subagent_model
    logger.info("%s uses model %s at %s", role, model, settings.llm_url)
    uvicorn.run(create_app(settings), host=args.host, port=port)
