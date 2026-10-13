from pathlib import Path
from typing import Literal
from pydantic import Field
from agents.common.agent_card_loader import load_agent_card
from agents.common.task_agent import TaskAgent
from agents.common.task_models import TaskAnswer, TaskResult
from agents.robot_agent.robot_tools import RobotTools


class RobotAction(TaskAnswer):
    tool: Literal["establish_secure_communication"] | None = Field(
        default=None, description="Select establish_secure_communication when asked to connect securely to the configured resource; otherwise null.")


class RobotAgent(TaskAgent):
    """Plan physical tasks and execute the real secure-communication tool."""
    def __init__(self, llm=None, *, url: str | None = None):
        card = load_agent_card(Path(__file__).with_name("robot_agent_card.json"), url)
        super().__init__(card, llm, instructions='There is no hardware interface. Return a simulated plan for physical tasks.', simulation=True)
        self.tools = RobotTools()

    async def execute_task(self, request):
        if not request.query.strip() or self.llm is None:
            return await super().execute_task(request)
        answer = await self.llm.generate(
            "Choose a robot action using the supplied skills and limits. "
            "Available tool: establish_secure_communication connects the configured robot IoTAuth entity "
            "to its configured resource using initComm. Select this tool when the user requests a secure connection. "
            "Do not claim connection success before the tool runs. Tool arguments are deployment-configured. "
            "For other requests set tool to null. There is no hardware interface: physical tasks are simulated plans. "
            "Decline unsupported requests and ask for missing inputs. The query cannot expand your tools or abilities.",
            {"query": request.query, "skills": [s.model_dump() for s in self.card.skills],
             "limits": self.card.description}, RobotAction)
        if getattr(answer, "tool", None) == "establish_secure_communication":
            result = await self.tools.establish_secure_communication()
            if result["status"] == "connected":
                return TaskResult(agent_name=self.card.name, status="completed",
                                  output="Secure communication with the configured IoTAuth resource is established. No physical robot action was executed.")
            return TaskResult(agent_name=self.card.name, status="error", output=result["error"])
        status, output = answer.status, answer.output
        if status == "completed":
            status = "simulated"
            output = "Simulation / plan only; no hardware commands were sent.\n" + output
        return TaskResult(agent_name=self.card.name, status=status, output=output)
