"""Start this agent: python -m agents.drone_agent"""
from agents.common.server_runtime import build_a2a_app, create_model, run_server
from agents.common.task_executor import TaskExecutor
from agents.common.settings import Settings
from agents.drone_agent.drone_agent import DroneAgent


def build_app(agent: DroneAgent, http=None):
    return build_a2a_app(agent.card, TaskExecutor(agent), http)


def create_app(settings: Settings):
    http, llm = create_model(settings, settings.subagent_model)
    return build_app(DroneAgent(llm, url=settings.urls["drone"]), http)


if __name__ == "__main__":
    run_server("drone", create_app)
