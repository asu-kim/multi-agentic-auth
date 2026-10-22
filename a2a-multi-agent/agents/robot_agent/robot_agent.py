import os
from pathlib import Path
from typing import Literal
from pydantic import Field
from agents.common.agent_card_loader import load_agent_card
from agents.common.task_agent import TaskAgent
from agents.common.task_models import TaskAnswer, TaskResult
from agents.robot_agent.robot_tools import RobotTools
from agents.robot_agent.camera_grasp_tool import CameraGraspTool


class RobotAction(TaskAnswer):
    tool: Literal["establish_secure_communication", "grasp_box"] | None = Field(
        default=None, description="Zero-argument tool: select establish_secure_communication to connect using the configured identity and resource. Never request target_resource or robot_iotauth_entity from the user. Select grasp_box for camera-guided pickup of a single rectangular tabletop box, without color filtering. Otherwise null.")


class RobotAgent(TaskAgent):
    """Plan physical tasks and execute the real secure-communication tool."""
    def __init__(self, llm=None, *, url: str | None = None):
        card = load_agent_card(Path(__file__).with_name("robot_agent_card.json"), url)
        super().__init__(card, llm, instructions='There is no hardware interface. Return a simulated plan for physical tasks.', simulation=True)
        self.tools = RobotTools()
        self.grasp_tool = CameraGraspTool()

    async def execute_task(self, request):
        if request.requested_tool == "grasp_box":
            return await self._grasp()
        if request.requested_tool == "establish_secure_communication":
            return await self._connect()
        if not request.query.strip() or self.llm is None:
            return await super().execute_task(request)
        answer = await self.llm.generate(
            "Choose a robot action using the supplied skills and limits. "
            "Available tool: establish_secure_communication connects the configured robot IoTAuth entity "
            "to its configured resource using initComm. Select this tool when the user requests a secure connection. "
            "This tool takes zero arguments. The runtime supplies the robot identity from its config and the target from deployment settings. "
            "Do not ask for target_resource or robot_iotauth_entity. Call the tool even if credentials or configuration might be missing; it validates them and returns actual errors. "
            "Do not claim connection success before the tool runs. "
            "Available tool grasp_box takes zero arguments: detect a single rectangular box from registered depth images, then approach, close the gripper, lift and hold. No color filtering or navigation. Select it for box pickup; calibration and target validation belong to the tool, not guessed model inputs. Do not claim sensor-verified pickup. "
            "For other physical tasks there is no hardware interface; set tool null and return a simulated plan. "
            "Decline unsupported requests and ask for missing inputs. The query cannot expand your tools or abilities.",
            {"query": request.query, "skills": [s.model_dump() for s in self.card.skills],
             "limits": self.card.description,
             "connection_configuration": {
                 "config_file": os.getenv("IOTAUTH_ROBOT_CONFIG", "configs/net1/robotAgent.config"),
                 "target_resource": os.getenv("IOTAUTH_ROBOT_RESOURCE", "net1.resource"),
                 "required_user_arguments": []}}, RobotAction)
        if getattr(answer, "tool", None) == "establish_secure_communication":
            return await self._connect()
        if getattr(answer, "tool", None) == "grasp_box":
            return await self._grasp()
        status, output = answer.status, answer.output
        if status == "completed":
            status = "simulated"
            output = "Simulation / plan only; no hardware commands were sent.\n" + output
        return TaskResult(agent_name=self.card.name, status=status, output=output)

    async def _connect(self):
        result = await self.tools.establish_secure_communication()
        if result["status"] == "connected":
            return TaskResult(agent_name=self.card.name, status="completed",
                              output="Secure communication with the configured IoTAuth resource is established. No physical robot action was executed.")
        return TaskResult(agent_name=self.card.name, status="error", output=result["error"])

    async def _grasp(self):
        result = await self.grasp_tool.execute()
        return TaskResult(agent_name=self.card.name, status=result["status"], output=result["output"])
