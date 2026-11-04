from pathlib import Path
from agents.common.agent_card_loader import load_agent_card
from agents.common.task_agent import TaskAgent


class RoverAgent(TaskAgent):
    """Publish warehouse rover capabilities and execute shared delivery tasks."""
    worker = "ROVER"
    default_config = "roverAgent"

    def __init__(self, llm=None, *, url: str | None = None):
        card = load_agent_card(Path(__file__).with_name("rover_agent_card.json"), url)
        super().__init__(card, llm, instructions=(
            "You are the warehouse rover agent. Your modeled capabilities are "
            "reach bottom shelf and carry light items. Use the shared delivery action for retrieval through a gate "
            "into the secure zone. For other physical tasks, return a simulated plan. "
        ), simulation=True)
