"""Start this agent: python -m agents.robot_agent"""
from agents.common.server_runtime import build_a2a_app, create_model, run_server
from agents.common.task_executor import TaskExecutor
from agents.common.settings import Settings
from agents.robot_agent.robot_agent import RobotAgent


def build_app(agent: RobotAgent, http=None):
    return build_a2a_app(agent.card, TaskExecutor(agent), http)


def create_app(settings: Settings):
    http, llm = create_model(settings, settings.subagent_model)
    return build_app(RobotAgent(llm, url=settings.urls["robot"]), http)


if __name__ == "__main__":
    run_server("robot", create_app)
