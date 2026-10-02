import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from agents.common.agent_card_loader import load_agent_card
from agents.common.negotiation_models import NegotiationRequest
from agents.common.settings import Settings
from agents.robot_agent.robot_agent import RobotAgent
from agents.robot_agent.__main__ import build_app as build_robot_app


class AgentStructureTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_entry_point_publishes_its_own_stored_card(self):
        with patch('agents.common.settings.load_dotenv'), patch.dict('os.environ', {}, clear=True):
            settings = Settings.from_env()
        for index, role in enumerate(('manager', 'language', 'analytics', 'robot')):
            module = importlib.import_module(f'agents.{role}_agent.__main__')
            settings.urls[role] = f'http://127.0.0.1:{32000 + index}/'
            expected = json.loads(Path(module.__file__).with_name(f'{role}_agent_card.json').read_text())
            expected['url'] = settings.urls[role]
            app = module.create_app(settings)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url=settings.urls[role]) as client:
                    response = await client.get('/.well-known/agent-card.json')
                    response.raise_for_status()
            self.assertEqual(response.json(), expected)

    async def test_card_edit_changes_published_and_negotiated_capabilities(self):
        module = importlib.import_module('agents.robot_agent.robot_agent')
        data = json.loads(Path(module.__file__).with_name('robot_agent_card.json').read_text())
        data['skills'] = [skill for skill in data['skills'] if skill['id'] == 'mobility']
        with tempfile.TemporaryDirectory() as directory:
            card_path = Path(directory) / 'robot_agent_card.json'
            card_path.write_text(json.dumps(data))
            with patch.object(module, 'load_agent_card', side_effect=lambda path, url: load_agent_card(card_path, url)):
                agent = RobotAgent(llm=None)
            app = build_robot_app(agent)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url=agent.card.url) as client:
                card = (await client.get('/.well-known/agent-card.json')).json()
            self.assertEqual([s['id'] for s in card['skills']], ['mobility'])
            request = NegotiationRequest(action='propose', request_id='test', query='Pick up a box',
                                         required_capabilities=['picking'])
            reply = await agent.negotiate(request, 'test-context')
            self.assertEqual(reply.status, 'declined')
            self.assertIn('picking', reply.reason)


if __name__ == '__main__':
    unittest.main()
