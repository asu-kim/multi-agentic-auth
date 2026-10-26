import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from agents.common.search_models import SearchRequest
from agents.manager_agent.iotauth_delegate import IoTAuthDelegate
from agents.manager_agent.manager_tools import AgentAddress, CardAssessment, Finish, ManagerTools
from agents.robot_agent.robot_agent import RobotAgent

ROBOT = 'http://127.0.0.1:10003/'


class DelegationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        (self.directory / 'manager.config').write_text('{}')
        self.env = patch.dict(os.environ, {
            'IOTAUTH_ENTITY_DIR': str(self.directory), 'IOTAUTH_MANAGER_CONFIG': 'manager.config',
            'IOTAUTH_NODE': sys.executable, 'IOTAUTH_TIMEOUT_SECONDS': '2',
            'IOTAUTH_AGENT_GROUPS': json.dumps({ROBOT: 'RobotAgents',
                'http://127.0.0.1:10001/': 'LanguageAgents'})})
        self.env.start()
        self.addCleanup(self.env.stop)

    def entity(self, body, subject='RobotAgents'):
        # A real subprocess tests the stdin/stdout protocol, with no Auth side effects.
        (self.directory / 'user.js').write_text(
            "import sys, json, time\nprint('initializing...', flush=True)\n"
            "print('current parameters: {}', flush=True)\n"
            "print('IOTAUTH_READY', flush=True)\n"
            "command = sys.stdin.readline()\n"
            f"assert command == 'delegateAuthority {subject} Resources 1*day\\n'\n" + body)

    async def test_confirmed_grant(self):
        self.entity("print('received privilege response!', flush=True)\n"
                    "print('IOTAUTH_PRIVILEGE ' + json.dumps({'communicationPolicy': "
                    "{'action': 'DelegationGrant', 'newCommunicationPolicy': "
                    "{'IsDelegated': 1, 'Target': 'ResourceA'}}}), flush=True)\n"
                    "print('Finished privilege request', flush=True)\ntime.sleep(10)\n")
        result = await IoTAuthDelegate().delegate(ROBOT)
        self.assertEqual(result['status'], 'delegated')
        self.assertEqual(result['communication_policy']['newCommunicationPolicy']['Target'], 'ResourceA')

    async def test_logs_alone_are_not_success(self):
        self.entity("print('received privilege response!')\nprint('Finished privilege request')\n")
        self.assertEqual((await IoTAuthDelegate().delegate(ROBOT))['status'], 'error')

    async def test_wrong_action_rejected(self):
        self.entity("print('received privilege response!')\n"
                    "print('IOTAUTH_PRIVILEGE ' + json.dumps({'communicationPolicy': "
                    "{'action': 'DelegationRevoke', 'newCommunicationPolicy': {'IsDelegated': 1}}}))\n"
                    "print('Finished privilege request')\n")
        self.assertEqual((await IoTAuthDelegate().delegate(ROBOT))['status'], 'error')

    async def test_timeout_and_auth_error(self):
        self.entity('time.sleep(10)\n')
        delegate = IoTAuthDelegate()
        delegate.timeout = .15
        result = await delegate.delegate(ROBOT)
        self.assertEqual(result['status'], 'error')
        self.assertTrue(result['request_sent'])
        self.entity("print('IOTAUTH_ERROR denied', flush=True)\n")
        self.assertEqual((await IoTAuthDelegate().delegate(ROBOT))['error'], 'denied')

    async def test_missing_config_and_unmapped_agent(self):
        (self.directory / 'manager.config').unlink()
        self.assertIn('config not found', (await IoTAuthDelegate().delegate(ROBOT))['error'])
        result = await IoTAuthDelegate().delegate('http://127.0.0.1:10002/')
        self.assertEqual(result['status'], 'error')
        self.assertFalse(result['request_sent'])

    async def test_guard_and_duplicate_calls(self):
        peer = AsyncMock()
        peer.discover.return_value = RobotAgent(url=ROBOT).card
        manager = ManagerTools(SearchRequest(query='Pick up a box', required_capabilities=['picking']),
                               (ROBOT,), peer)
        args = AgentAddress(agent_url=ROBOT)
        with self.assertRaises(ValueError):
            await manager.delegate_authority(args)
        await manager.discover_agent(args)
        await manager.assess_agent(CardAssessment(agent_url=ROBOT, suitable=True, reason='Fits payload limit'))
        with patch.object(IoTAuthDelegate, 'delegate', new_callable=AsyncMock,
                          return_value={'status': 'error', 'error': 'timeout'}) as call:
            await manager.delegate_authority(args)
            await manager.delegate_authority(args)
            call.assert_awaited_once()
        result = await manager.finish_search(Finish(status='found', agent_url=ROBOT))
        self.assertEqual(result['status'], 'error')

    async def test_selected_non_robot_subject_is_sent(self):
        self.entity("print('received privilege response!', flush=True)\n"
                    "print('IOTAUTH_PRIVILEGE ' + json.dumps({'communicationPolicy': "
                    "{'action': 'DelegationGrant', 'newCommunicationPolicy': {'IsDelegated': 1}}}), flush=True)\n"
                    "print('Finished privilege request', flush=True)\n", subject='LanguageAgents')
        from agents.language_agent.language_agent import LanguageAgent
        url = 'http://127.0.0.1:10001/'
        peer = AsyncMock()
        peer.discover.return_value = LanguageAgent(url=url).card
        manager = ManagerTools(SearchRequest(query='Translate text', required_capabilities=['translation']),
                               (ROBOT, url), peer)
        await manager.discover_agent(AgentAddress(agent_url=url))
        await manager.assess_agent(CardAssessment(agent_url=url, suitable=True, reason='Supports translation'))
        result = await manager.delegate_authority(AgentAddress(agent_url=url.rstrip('/')))
        self.assertEqual(result['status'], 'delegated')
        self.assertEqual(result['subject'], 'LanguageAgents')
        peer.send.return_value = {'agent_name': 'Language Agent', 'status': 'completed', 'output': 'Translation done'}
        await manager.dispatch_task(AgentAddress(agent_url=url))
        final = await manager.finish_search(Finish(status='found', agent_url=url))
        self.assertIn('LanguageAgents', final['summary'])
        self.assertNotIn('RobotAgents', final['summary'])

    async def test_invalid_subject_cannot_inject_commands(self):
        for value in ['LanguageAgents Resources', 'LanguageAgents\nrevoke', '']:
            with self.subTest(value=value), patch.dict(os.environ, {
                'IOTAUTH_AGENT_GROUPS': json.dumps({ROBOT: value})
            }):
                with self.assertRaises(ValueError):
                    IoTAuthDelegate()
