import unittest
from unittest.mock import AsyncMock
import httpx
from agents.common.a2a_client import A2APeer
from agents.common.search_models import SearchRequest
from agents.manager_agent.manager_tools import ManagerTools, AgentAddress, CardAssessment, Finish
from agents.robot_agent.robot_agent import RobotAgent, RobotAction
from agents.robot_agent.__main__ import build_app

URL = 'http://127.0.0.1:10003/'

class DispatchTests(unittest.IsolatedAsyncioTestCase):
    async def manager(self, peer):
        manager = ManagerTools(SearchRequest(query='Find an agent to pick up a box', required_capabilities=['picking']), (URL,), peer)
        await manager.discover_agent(AgentAddress(agent_url=URL))
        await manager.assess_agent(CardAssessment(agent_url=URL, suitable=True, reason='Has picking capability'))
        return manager

    async def test_real_a2a_robot_handoff(self):
        llm = AsyncMock()
        llm.generate.return_value = RobotAction(tool='establish_secure_communication', status='completed', output='Connect')
        robot = RobotAgent(llm, url=URL)
        robot.tools = AsyncMock()
        robot.tools.establish_secure_communication.return_value = {'status':'connected'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app(robot))) as http:
            manager = await self.manager(A2APeer(http))
            args = AgentAddress(agent_url=URL)
            with self.assertRaises(ValueError):
                await manager.dispatch_task(args)
            robot.tools.establish_secure_communication.assert_not_awaited()
            manager.delegations[URL.rstrip('/')] = {'status':'delegated','subject':'RobotAgents'}
            with self.assertRaises(ValueError):
                await manager.finish_search(Finish(status='found', agent_url=URL))
            response = await manager.dispatch_task(args)
            self.assertEqual(response['status'], 'completed')
            await manager.dispatch_task(args)
            robot.tools.establish_secure_communication.assert_awaited_once()
            llm.generate.assert_not_awaited()
            final = await manager.finish_search(Finish(status='found', agent_url=URL))
            self.assertEqual(final['status'], 'found')
            self.assertIn('Worker reported completed', final['summary'])
            self.assertEqual(manager.finished.task_result, response)

    async def test_failed_dispatch_not_retried(self):
        peer = AsyncMock()
        peer.discover.return_value = RobotAgent(url=URL).card
        peer.send.side_effect = TimeoutError('worker timed out')
        manager = await self.manager(peer)
        manager.delegations[URL.rstrip('/')] = {'status':'delegated','subject':'RobotAgents'}
        args = AgentAddress(agent_url=URL)
        self.assertEqual((await manager.dispatch_task(args))['status'], 'error')
        await manager.dispatch_task(args)
        peer.send.assert_awaited_once()
        self.assertEqual((await manager.finish_search(Finish(status='found', agent_url=URL)))['status'], 'error')
