import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from agents.robot_agent.robot_tools import RobotTools
from agents.robot_agent.robot_agent import RobotAgent, RobotAction
from agents.common.task_models import TaskRequest


class RobotToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        (self.directory / 'robot.config').write_text('{}')
        self.env = patch.dict(os.environ, {
            'IOTAUTH_ENTITY_DIR': str(self.directory), 'IOTAUTH_NODE': sys.executable,
            'IOTAUTH_ROBOT_CONFIG': 'robot.config', 'IOTAUTH_ROBOT_RESOURCE': 'net1.resource',
            'IOTAUTH_TIMEOUT_SECONDS': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.tools = RobotTools()
        self.addAsyncCleanup(self.tools.close)

    def entity(self, body):
        (self.directory / 'user.js').write_text(
            "import sys, time\nassert sys.argv[1:] == ['robot.config']\nprint('current parameters: {', flush=True)\nprint('  numKeysPerRequest: 1', flush=True)\nprint('}', flush=True)\n"
            "assert sys.stdin.readline() == 'initComm net1.resource\\n'\n" + body)

    async def test_connect_reuse_and_close(self):
        self.entity("print('switching to IN_COMM', flush=True)\ntime.sleep(10)\n")
        self.assertEqual((await self.tools.establish_secure_communication())['status'], 'connected')
        process = self.tools.process
        self.assertIsNone(process.returncode)
        self.assertTrue((await self.tools.establish_secure_communication())['reused'])
        self.assertIs(process, self.tools.process)
        await self.tools.close()
        self.assertIsNotNone(process.returncode)
        self.assertFalse(self.tools.connected)

    async def test_error_exit_and_timeout(self):
        for body in ["print('Handler: Error in secure comm - details: denied', flush=True)\n", 'pass\n', 'time.sleep(10)\n']:
            with self.subTest(body=body):
                self.entity(body)
                result = await self.tools.establish_secure_communication()
                self.assertEqual(result['status'], 'error')
                self.assertIsNone(self.tools.process)

    async def test_missing_config(self):
        (self.directory / 'robot.config').unlink()
        self.assertEqual((await self.tools.establish_secure_communication())['status'], 'error')

    async def test_robot_dispatches_and_reports_observed_outcome(self):
        llm = AsyncMock()
        llm.generate.return_value = RobotAction(status='completed', output='Model claim', tool='establish_secure_communication')
        agent = RobotAgent(llm)
        agent.tools = AsyncMock()
        for outcome, status in [({'status':'connected'}, 'completed'), ({'status':'error','error':'Auth denied'}, 'error')]:
            agent.tools.establish_secure_communication.return_value = outcome
            result = await agent.execute_task(TaskRequest(query='Connect securely'))
            self.assertEqual(result.status, status)
            self.assertNotIn('Model claim', result.output)
        self.assertEqual(agent.tools.establish_secure_communication.await_count, 2)

    async def test_initialization_success_alone_is_not_connection_success(self):
        self.entity("print('Handler: communication initialization succeeded', flush=True)\ntime.sleep(10)\n")
        result = await self.tools.establish_secure_communication()
        self.assertEqual(result['status'], 'error')
        self.assertFalse(self.tools.connected)

    async def test_disconnect_clears_connection_state(self):
        self.entity("print('switching to IN_COMM', flush=True)\ntime.sleep(.1)\n"
                    "print('Handler: secure connection with the server closed.', flush=True)\ntime.sleep(10)\n")
        result = await self.tools.establish_secure_communication()
        self.assertEqual(result['status'], 'connected')
        import asyncio
        async with asyncio.timeout(1):
            while self.tools.connected:
                await asyncio.sleep(.01)
        self.assertFalse(self.tools.connected)

    async def test_explicit_tool_bypasses_model_missing_input_refusal(self):
        llm = AsyncMock()
        llm.generate.return_value = RobotAction(status='declined', output='Missing target_resource', tool=None)
        agent = RobotAgent(llm)
        agent.tools = AsyncMock()
        request = TaskRequest(query='Connect to the configured resource', requested_tool='establish_secure_communication')
        for outcome, expected in [({'status': 'connected'}, 'completed'),
                                  ({'status': 'error', 'error': 'Robot config missing'}, 'error')]:
            agent.tools.establish_secure_communication.return_value = outcome
            result = await agent.execute_task(request)
            self.assertEqual(result.status, expected)
        llm.generate.assert_not_awaited()
        self.assertEqual(agent.tools.establish_secure_communication.await_count, 2)
