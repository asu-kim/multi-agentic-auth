from pathlib import Path
from agents.common.agent_card_loader import load_agent_card
from agents.common.task_agent import TaskAgent


class DroneAgent(TaskAgent):
    """Publish warehouse drone capabilities and execute shared delivery tasks."""
    worker = "DRONE"
    default_config = "droneAgent"

    def __init__(self, llm=None, *, url: str | None = None):
        card = load_agent_card(Path(__file__).with_name("drone_agent_card.json"), url)
        super().__init__(card, llm, instructions=(
            "You are the warehouse drone agent. Your modeled capabilities are "
            "reach top shelf and carry light items. Use the shared delivery action for retrieval through a gate into "
            "the secure zone. For other physical tasks, return a simulated plan. "
        ), simulation=True)
