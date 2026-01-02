import uvicorn
import logging
from typing import Any

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
from agent_executor import (
    Agent1Executor,  # type: ignore[import-untyped]
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SESSION_EXT_URI = "https://asu-kim.example/ext/sst-session-key/v1"


if __name__ == '__main__':
    # --8<-- [start:AgentSkill]
    skill = AgentSkill(
        id='hello_world',
        name='Returns hello world',
        description='just returns hello world',
        tags=['hello world'],
        examples=['hi', 'hello world'],
    )
    # --8<-- [end:AgentSkill]



    # --8<-- [start:AgentCard]
    public_agent_card = AgentCard(
        name='Agent 1',
        description='Agent with Session Key ID',
        url='http://localhost:9999/',
        version='1.0.0',
        default_input_modes=['text'],
        default_output_modes=['text'],
        capabilities=AgentCapabilities(
            streaming=True,
            extensions=[
                {
                    "uri": SESSION_EXT_URI,
                    "params": {
                        "agentGroup": "LowTrustAgents" # group or agent name
                    },
                }
            ],
        ),
        skills=[skill],  # Only the basic skill for the public card
        supports_authenticated_extended_card=True,
    )
    # --8<-- [end:AgentCard]


    request_handler = DefaultRequestHandler(
        agent_executor=Agent1Executor(card=public_agent_card),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=public_agent_card,
        http_handler=request_handler,
    )

    uvicorn.run(server.build(), host='0.0.0.0', port=9999)
