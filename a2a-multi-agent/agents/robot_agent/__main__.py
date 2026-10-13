"""Start this agent: python -m agents.robot_agent"""
from contextlib import asynccontextmanager
from agents.common.server_runtime import build_a2a_app, create_model, run_server
from agents.common.task_executor import TaskExecutor
from agents.common.settings import Settings
from agents.robot_agent.robot_agent import RobotAgent


def build_app(agent: RobotAgent, http=None):
    app = build_a2a_app(agent.card, TaskExecutor(agent), http)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app):
            try:
                yield
            finally:
                await agent.tools.close()

    app.router.lifespan_context = lifespan
    return app


def create_app(settings: Settings):
    http, llm = create_model(settings, settings.subagent_model)
    return build_app(RobotAgent(llm, url=settings.urls["robot"]), http)


if __name__ == "__main__":
    run_server("robot", create_app)
