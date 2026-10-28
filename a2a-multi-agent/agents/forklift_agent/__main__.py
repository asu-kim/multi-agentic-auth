"""Start this agent: python -m agents.forklift_agent"""
from contextlib import asynccontextmanager
from agents.common.server_runtime import build_a2a_app, create_model, run_server
from agents.common.task_executor import TaskExecutor
from agents.common.settings import Settings
from agents.forklift_agent.forklift_agent import ForkliftAgent


def build_app(agent: ForkliftAgent, http=None):
    app = build_a2a_app(agent.card, TaskExecutor(agent), http)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app):
            try:
                yield
            finally:
                await agent.grasp_tool.close()
                await agent.tools.close()

    app.router.lifespan_context = lifespan
    return app


def create_app(settings: Settings):
    http, llm = create_model(settings, settings.subagent_model)
    return build_app(ForkliftAgent(llm, url=settings.urls["forklift"]), http)


if __name__ == "__main__":
    run_server("forklift", create_app)
