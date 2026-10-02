"""Start this agent: python -m capability_team.robot_agent"""
from capability_team.common.server_runtime import build_a2a_app, create_model, run_server
from capability_team.common.settings import Settings
from capability_team.robot_agent.robot_agent import RobotAgent
from capability_team.robot_agent.robot_agent_executor import RobotAgentExecutor


def build_app(agent: RobotAgent, http=None):
    return build_a2a_app(agent.card, RobotAgentExecutor(agent), http)


def create_app(settings: Settings):
    http, llm = create_model(settings, settings.subagent_model)
    agent = RobotAgent(llm, url=settings.urls["robot"], available=settings.robot_available)
    return build_app(agent, http)


if __name__ == "__main__":
    run_server("robot", create_app)
