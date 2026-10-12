import importlib
import json
import unittest
from unittest.mock import patch

import httpx

from agents.common.a2a_client import A2APeer
from agents.common.settings import Settings
from agents.common.task_models import TaskAnswer, TaskRequest, TaskResult
from agents.common.ollama_client import ModelError


class WorkerLLM:
    def __init__(self, status='completed', output='Task result'):
        self.calls = []
        self.answer = TaskAnswer(status=status, output=output)
        self.error = None

    async def generate(self, system, payload, schema):
        self.calls.append((system, payload, schema))
        if self.error:
            raise self.error
        return self.answer


class WorkerTaskTests(unittest.IsolatedAsyncioTestCase):
    async def make_request(self, role, llm, data=None):
        module = importlib.import_module(f'agents.{role}_agent.__main__')
        agent = getattr(module, role.title()+'Agent')(llm)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.build_app(agent))) as http:
            peer = A2APeer(http)
            card = await peer.discover(agent.card.url)
            self.assertEqual(llm.calls, [])
            result = await peer.send(card, data or TaskRequest(query='Test task').model_dump(), 'task-context')
        return TaskResult.model_validate(result)

    async def test_all_workers_execute_only_after_explicit_request(self):
        for role in ('language', 'analytics', 'robot'):
            with self.subTest(role=role):
                llm = WorkerLLM(output='Generated response')
                result = await self.make_request(role, llm)
                self.assertEqual(result.status, 'simulated' if role == 'robot' else 'completed')
                self.assertIn('Generated response', result.output)
                self.assertEqual(len(llm.calls), 1)
                self.assertTrue(llm.calls[0][1]['skills'])
                if role == 'robot':
                    self.assertIn('no hardware commands', result.output)
                    self.assertIn('no hardware interface', llm.calls[0][0])

    async def test_missing_input_and_declines_preserved(self):
        for status in ('input_required', 'declined'):
            result = await self.make_request('robot', WorkerLLM(status, 'Provide the box weight.'))
            self.assertEqual(result.status, status)

    async def test_model_errors_return_structured_result(self):
        llm = WorkerLLM()
        llm.error = ModelError('worker model missing')
        with self.assertLogs('agents.common.task_executor', level='ERROR'):
            result = await self.make_request('language', llm)
        self.assertEqual(result.status, 'error')
        self.assertIn('worker model missing', result.output)

    async def test_plain_text_and_invalid_old_protocol(self):
        module = importlib.import_module('agents.language_agent.__main__')
        llm = WorkerLLM(output='안녕하세요')
        agent = module.LanguageAgent(llm)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.build_app(agent))) as http:
            async def send(parts):
                return await http.post(agent.card.url, json={'jsonrpc':'2.0', 'id':'test',
                    'method':'message/send', 'params':{'message':{'role':'user', 'messageId':'test', 'parts':parts}}})
            response = await send([{'kind':'text', 'text':'Translate Hello to Korean'}])
            self.assertNotIn('error', response.json())
            self.assertEqual(llm.calls[0][1]['query'], 'Translate Hello to Korean')
            response = await send([{'kind':'data', 'data':{'protocol':'capability-negotiation/v1', 'action':'propose', 'query':'Hello'}}])
            self.assertIn('error', response.json())
            self.assertEqual(len(llm.calls), 1)

    async def test_no_model_and_blank_query(self):
        from agents.language_agent.language_agent import LanguageAgent
        result = await LanguageAgent().execute_task(TaskRequest(query='Translate Hello'))
        self.assertEqual(result.status, 'error')
        llm = WorkerLLM()
        result = await LanguageAgent(llm).execute_task(TaskRequest(query='   '))
        self.assertEqual(result.status, 'input_required')
        self.assertEqual(llm.calls, [])

    async def test_worker_model_config_and_http_lifecycle(self):
        with patch('agents.common.settings.load_dotenv'), patch.dict('os.environ', {'SUBAGENT_MODEL':'custom-worker'}, clear=True):
            settings = Settings.from_env()
        for role in ('language', 'analytics', 'robot'):
            module = importlib.import_module(f'agents.{role}_agent.__main__')
            http = httpx.AsyncClient()
            with patch.object(module, 'create_model', return_value=(http, WorkerLLM())) as create:
                app = module.create_app(settings)
                create.assert_called_once_with(settings, 'custom-worker')
                async with app.router.lifespan_context(app):
                    self.assertFalse(http.is_closed)
                self.assertTrue(http.is_closed)
