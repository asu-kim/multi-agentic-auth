from capability_team.common.negotiation_executor import NegotiationExecutor
from capability_team.robot_agent.robot_agent import RobotAgent


class RobotAgentExecutor(NegotiationExecutor):
    """A2A request adapter for RobotAgent."""

    def __init__(self, agent: RobotAgent):
        super().__init__(agent)
