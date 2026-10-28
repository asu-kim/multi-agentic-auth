"""Start this agent: python -m agents.rover_agent"""
from agents.common.server_runtime import build_a2a_app, create_model, run_server
from agents.common.task_executor import TaskExecutor
from agents.common.settings import Settings
from agents.rover_agent.rover_agent import RoverAgent


def build_app(agent: RoverAgent, http=None):
    return build_a2a_app(agent.card, TaskExecutor(agent), http)


def create_app(settings: Settings):
    http, llm = create_model(settings, settings.subagent_model)
    return build_app(RoverAgent(llm, url=settings.urls["rover"]), http)


if __name__ == "__main__":
    run_server("rover", create_app)
