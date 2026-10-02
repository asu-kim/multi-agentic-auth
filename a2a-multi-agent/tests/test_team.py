import asyncio
import copy
import io
import json
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import httpx

from agents.common.settings import Settings
from agents.common.ollama_client import ModelError, OllamaJSON
from agents.manager_agent.manager_agent import ManagerAgent
from agents.manager_agent.__main__ import build_app as build_manager_app
from agents.language_agent.language_agent import LanguageAgent
from agents.language_agent.__main__ import build_app as build_language_app
from agents.analytics_agent.analytics_agent import AnalyticsAgent
from agents.analytics_agent.__main__ import build_app as build_analytics_app
from agents.robot_agent.robot_agent import RobotAgent
from agents.robot_agent.__main__ import build_app as build_robot_app
from agents.common.negotiation_models import Assessment, NegotiationRequest, SearchRequest, SearchResult
from agents.common.a2a_client import A2APeer
from agents.client import print_result

LANGUAGE = 'http://127.0.0.1:10001/'
ANALYTICS = 'http://127.0.0.1:10002/'
ROBOT = 'http://127.0.0.1:10003/'


def tool(name, **arguments):
    return {'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': name, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}


def discover(url):
    return tool('discover_agent', agent_url=url)


def finish(status='found', url=ROBOT):
    return tool('finish_search', status=status, agent_url=url)


def match_script(url=ROBOT, capabilities=None):
    return [discover(url), tool('set_requirements', capabilities=capabilities or ['picking', 'mobility']),
            tool('propose_task', agent_url=url), tool('confirm_offer', agent_url=url), finish(url=url)]


def all_cards():
    return [discover(url) for url in (LANGUAGE, ANALYTICS, ROBOT)]


class FakeLLM:
    """Sub-agent inference only; the A2A transport uses the real SDK."""
    def __init__(self, answer=None, error=None):
        self.answer = answer or Assessment(can_help=True, reason='Within my capabilities and limits.')
        self.error, self.calls = error, []

    async def generate(self, system, payload, schema):
        self.calls.append((payload, schema))
        if self.error:
            raise self.error
        return self.answer


class ScriptedManagerLLM:
    """Chosen model actions for a scenario. No discovery/negotiation logic lives here."""
    def __init__(self, script):
        self.script, self.calls, self.error = script, [], None

    async def chat(self, messages, tools):
        self.calls.append(copy.deepcopy(messages))
        if self.error:
            raise self.error
        index = sum(message['role'] == 'assistant' for message in messages)
        action = self.script[index] if index < len(self.script) else {'role': 'assistant', 'content': 'done'}
        if callable(action):
            action = action(messages)
        message = copy.deepcopy(action)
        for i, call in enumerate(message.get('tool_calls', [])):
            call['id'] = f'call_{index}_{i}'
        return message


class Network(httpx.AsyncBaseTransport):
    """Route HTTP to independent ASGI A2A servers without binding local test ports."""
    def __init__(self):
        self.apps, self.requests = {}, []

    async def handle_async_request(self, request):
        self.requests.append((str(request.url), request.method))
        app = self.apps.get(request.url.port)
        if app is None:
            raise httpx.ConnectError('Agent offline', request=request)
        return await httpx.ASGITransport(app=app).handle_async_request(request)


class TeamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.network = Network()
        self.http = httpx.AsyncClient(transport=self.network)
        self.peer = A2APeer(self.http)
        self.urls = (LANGUAGE, ANALYTICS, ROBOT)
        self.workers = {}
        for role, agent_class, build_app, url in [
            ('language', LanguageAgent, build_language_app, LANGUAGE),
            ('analytics', AnalyticsAgent, build_analytics_app, ANALYTICS),
            ('robot', RobotAgent, build_robot_app, ROBOT),
        ]:
            worker = agent_class(FakeLLM(), url=url)
            self.workers[role] = worker
            self.network.apps[httpx.URL(url).port] = build_app(worker)
        self.manager_llm = ScriptedManagerLLM(match_script())
        self.manager = ManagerAgent(self.urls, self.peer, self.manager_llm)
        self.network.apps[10000] = build_manager_app(self.manager)

    async def asyncTearDown(self):
        await self.http.aclose()

    async def ask(self, query, required=None):
        card = await self.peer.discover('http://127.0.0.1:10000/')
        data = SearchRequest(query=query, required_capabilities=required or []).model_dump()
        return SearchResult.model_validate(await self.peer.send(card, data, 'user-context'))

    async def test_model_can_choose_robot_first_and_finish_early_via_a2a(self):
        result = await self.ask('Pick up a 1 kg box and carry it indoors.')
        self.assertEqual(result.status, 'found')
        self.assertEqual(result.selected_agent.name, 'Robot Agent')
        self.assertEqual([a.status for a in result.attempts], ['offered', 'confirmed'])
        self.assertEqual(len(result.discovered_agents), 1)
        self.assertEqual(len(self.manager_llm.calls), 5)
        self.assertEqual(len(self.workers['robot'].llm.calls), 1)
        self.assertEqual([a['tool'] for a in result.manager_actions],
                         ['discover_agent', 'set_requirements', 'propose_task', 'confirm_offer', 'finish_search'])
        self.assertFalse(any('10001' in url or '10002' in url for url, _ in self.network.requests))
        # Every next model turn sees the actual previous tool result.
        self.assertEqual(self.manager_llm.calls[1][-1]['role'], 'tool')
        self.assertEqual(json.loads(self.manager_llm.calls[1][-1]['content'])['agent_card']['name'], 'Robot Agent')

    async def test_model_can_define_requirements_before_discovery(self):
        script = match_script()
        script[0], script[1] = script[1], script[0]
        self.manager_llm.script = script
        result = await self.ask('Pick up a 1 kg box and move it indoors.')
        self.assertEqual(result.status, 'found')
        self.assertEqual(result.manager_actions[0]['tool'], 'set_requirements')

    async def test_brief_decision_summary_is_logged_and_returned(self):
        summary = 'I will inspect the robot card to check its advertised abilities.'
        self.manager_llm.script[0] = tool('discover_agent', agent_url=ROBOT, decision_summary=summary)
        with self.assertLogs('agents.manager_agent.manager_agent', level='INFO') as logs:
            result = await self.ask('Pick up a box and move it indoors')
        self.assertEqual(result.status, 'found')
        self.assertEqual(result.manager_actions[0]['decision_summary'], summary)
        self.assertEqual(result.manager_actions[0]['summary_source'], 'model')
        self.assertEqual(result.manager_actions[1]['summary_source'], 'system')
        self.assertTrue(any(summary in line for line in logs.output))
        output = io.StringIO()
        with redirect_stdout(output):
            print_result(result, trace=True)
        self.assertIn('Turn 1 [discover_agent]: ' + summary, output.getvalue())
        self.assertIn('(system)', output.getvalue())

    async def test_trace_flag_keeps_json_output_valid(self):
        result = await self.ask('Pick up a box and move it indoors')
        output = io.StringIO()
        with redirect_stdout(output):
            print_result(result, as_json=True, trace=True)
        parsed = json.loads(output.getvalue())
        self.assertEqual(parsed['status'], 'found')
        self.assertIn('decision_summary', parsed['manager_actions'][0])

    async def test_full_a2a_search_with_native_ollama_tool_protocol(self):
        director = ScriptedManagerLLM(match_script())
        requests = []

        async def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            answer = await director.chat(payload['messages'], payload['tools'])
            return httpx.Response(200, json={'choices': [{'message': answer}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            self.manager.llm = OllamaJSON(http, 'http://127.0.0.1:11435/v1', 'gpt-oss:20b', 'ollama', 30)
            result = await self.ask('Pick up a 1 kg box and carry it indoors')
        self.assertEqual(result.status, 'found')
        self.assertEqual(len(requests), 5)
        self.assertEqual([a.status for a in result.attempts], ['offered', 'confirmed'])
        self.assertEqual(json.loads(requests[4]['messages'][-1]['content'])['status'], 'confirmed')

    async def test_model_can_retry_a_failed_discovery(self):
        original = self.peer.discover
        failed = False

        async def discover_with_one_failure(url):
            nonlocal failed
            if url == ROBOT and not failed:
                failed = True
                raise RuntimeError('Temporary connection failure')
            return await original(url)

        self.peer.discover = discover_with_one_failure
        self.manager_llm.script = [discover(ROBOT)] + match_script()
        result = await self.ask('Pick up a box and move it indoors')
        self.assertEqual(result.status, 'found')
        self.assertIn('Temporary connection failure', result.manager_actions[0]['result']['error'])
        self.assertIn('agent_card', result.manager_actions[1]['result'])

    async def test_both_sample_agents_can_be_found(self):
        for capability, url, name in [('translation', LANGUAGE, 'Language Agent'),
                                      ('anomaly_detection', ANALYTICS, 'Analytics Agent')]:
            self.manager_llm.script = match_script(url, [capability])
            result = await self.ask('Find a specialist', [capability])
            self.assertEqual(result.status, 'found')
            self.assertEqual(result.selected_agent.name, name)

    async def test_unsupported_and_partial_matches_are_not_found(self):
        for caps in [['flying'], ['picking', 'translation']]:
            self.manager_llm.script = all_cards() + [finish('not_found', None)]
            result = await self.ask('Find an agent', caps)
            self.assertEqual(result.status, 'not_found')
            self.assertIsNone(result.selected_agent)
            self.assertEqual(result.attempts, [])

    async def test_inferred_unsupported_requirement_preserved(self):
        self.manager_llm.script = [tool('set_requirements', capabilities=['picking', 'flying'])] + all_cards() + [finish('not_found', None)]
        result = await self.ask('Pick up a box and fly it across town')
        self.assertEqual(result.required_capabilities, ['picking', 'flying'])
        self.assertEqual(result.status, 'not_found')

    async def test_worker_declines_infeasible_task(self):
        self.workers['robot'].llm.answer = Assessment(can_help=False, reason='20 kg exceeds 2 kg payload limit.')
        self.manager_llm.script = all_cards() + [tool('propose_task', agent_url=ROBOT), finish('not_found', None)]
        result = await self.ask('Pick up a 20 kg box', ['picking'])
        self.assertEqual(result.status, 'not_found')
        self.assertEqual(result.attempts[0].status, 'declined')

    async def test_model_reacts_to_decline_by_trying_another_agent(self):
        backup_url = 'http://127.0.0.1:10004/'
        backup = RobotAgent(FakeLLM(), url=backup_url)
        self.network.apps[10004] = build_robot_app(backup)
        self.manager.urls += (backup_url,)
        self.workers['robot'].available = False

        def choose_after_decline(messages):
            self.assertEqual(json.loads(messages[-1]['content'])['status'], 'declined')
            return discover(backup_url)

        self.manager_llm.script = [discover(ROBOT), tool('propose_task', agent_url=ROBOT),
            choose_after_decline, tool('propose_task', agent_url=backup_url),
            tool('confirm_offer', agent_url=backup_url), finish(url=backup_url)]
        result = await self.ask('Pick up a box', ['picking'])
        self.assertEqual(result.status, 'found')
        self.assertEqual(result.selected_agent.url, backup_url)
        self.assertEqual([a.status for a in result.attempts], ['declined', 'offered', 'confirmed'])

    async def test_offline_agent_does_not_prevent_other_match(self):
        del self.network.apps[10001]
        self.manager_llm.script = [discover(LANGUAGE)] + match_script(capabilities=['picking'])
        result = await self.ask('Pick up a box', ['picking'])
        self.assertEqual(result.status, 'found')
        self.assertEqual(result.attempts[0].status, 'error')

    async def test_offline_robot_reports_incomplete_search(self):
        del self.network.apps[10003]
        self.manager_llm.script = all_cards() + [finish('not_found', None)]
        result = await self.ask('Pick up a box', ['picking'])
        self.assertEqual(result.status, 'not_found')
        self.assertIn('incomplete', result.summary)

    async def test_model_failure_keeps_upstream_reason(self):
        self.workers['robot'].llm.error = ModelError("model 'llama3.2:3b' not found")
        self.manager_llm.script = all_cards() + [tool('propose_task', agent_url=ROBOT), finish('not_found', None)]
        result = await self.ask('Pick up a box', ['picking'])
        self.assertEqual(result.status, 'not_found')
        self.assertIn('llama3.2:3b', result.attempts[0].detail)
        self.assertIn('incomplete', result.summary)

    async def test_manager_failure_is_error(self):
        self.manager_llm.error = ModelError('manager model offline')
        result = await self.ask('Find a robot')
        self.assertEqual(result.status, 'error')
        self.assertIn('manager model offline', result.summary)

    async def test_model_can_ask_user_before_discovery(self):
        self.manager_llm.script = [tool('ask_user', question='Which ability do you need?')]
        result = await self.ask('Find someone')
        self.assertEqual(result.status, 'input_required')
        self.assertEqual(result.discovered_agents, [])
        self.assertEqual(result.summary, 'Which ability do you need?')

    async def test_unconfirmed_offer_cannot_be_reported_found(self):
        self.manager_llm.script = [discover(ROBOT), tool('propose_task', agent_url=ROBOT), finish(),
                                  tool('confirm_offer', agent_url=ROBOT), finish()]
        result = await self.ask('Pick up a box', ['picking'])
        self.assertEqual(result.status, 'found')
        self.assertIn('without a confirmed', result.manager_actions[2]['result']['error'])

    async def test_not_found_requires_roster_coverage_and_resolved_candidates(self):
        self.manager_llm.script = [finish('not_found', None)] + all_cards() + [finish('not_found', None)]
        self.manager.max_turns = len(self.manager_llm.script)
        result = await self.ask('Pick up a box', ['picking'])
        self.assertEqual(result.status, 'error')
        self.assertIn('remaining agents', result.manager_actions[0]['result']['error'])
        self.assertIn('Resolve matching', result.manager_actions[-1]['result']['error'])

    async def test_explicit_requirements_cannot_be_dropped(self):
        self.manager_llm.script = [tool('set_requirements', capabilities=['picking'])] + all_cards() + [finish('not_found', None)]
        result = await self.ask('Pick and fly', ['picking', 'flying'])
        self.assertEqual(result.status, 'not_found')
        self.assertEqual(result.required_capabilities, ['picking', 'flying'])
        self.assertIn('Cannot change', result.manager_actions[0]['result']['error'])

    async def test_invalid_tools_and_arguments_return_errors_to_model(self):
        bad_json = tool('discover_agent', agent_url=ROBOT)
        bad_json['tool_calls'][0]['function']['arguments'] = '{broken'
        self.manager_llm.script = [tool('execute_shell', command='anything'), bad_json,
            discover('http://not-in-roster:9999/')] + match_script()
        result = await self.ask('Pick up a box and carry it indoors')
        self.assertEqual(result.status, 'found')
        self.assertTrue(all('error' in a['result'] for a in result.manager_actions[:3]))
        self.assertFalse(any('not-in-roster' in url for url, _ in self.network.requests))

    async def test_fabricated_text_cannot_produce_success_and_loop_is_bounded(self):
        self.manager_llm.script = [{'role': 'assistant', 'content': 'Found a flying robot; task done.'}]
        self.manager.max_turns = 3
        result = await self.ask('Find a flying robot')
        self.assertEqual(result.status, 'error')
        self.assertIsNone(result.selected_agent)
        self.assertIn('3-turn limit', result.summary)
        self.assertEqual(len(self.manager_llm.calls), 3)
        self.assertEqual(len(result.manager_actions), 3)
        self.assertTrue(all(a['summary_source'] == 'system' for a in result.manager_actions))
        self.assertNotIn('Found a flying robot; task done.', json.dumps(result.manager_actions))

    async def test_total_deadline_is_enforced(self):
        async def slow_chat(messages, tools):
            await asyncio.sleep(1)
        self.manager_llm.chat = slow_chat
        self.manager.timeout = .01
        result = await self.ask('Find a robot')
        self.assertEqual(result.status, 'error')
        self.assertIn('deadline', result.summary)

    async def test_concurrent_searches_do_not_share_state(self):
        results = await asyncio.gather(self.ask('Pick up box A and carry it indoors'),
                                       self.ask('Pick up box B and carry it indoors'))
        self.assertTrue(all(r.status == 'found' for r in results))
        self.assertNotEqual(results[0].selected_agent.offer_id, results[1].selected_agent.offer_id)
        self.assertEqual([len(r.attempts) for r in results], [2, 2])
        self.assertEqual([len(r.manager_actions) for r in results], [5, 5])

    async def test_confirmation_requires_matching_offer_and_context(self):
        worker = self.workers['robot']
        request = NegotiationRequest(action='propose', request_id='abc', query='Pick up a 1 kg box',
                                     required_capabilities=['picking'])
        offered = await worker.negotiate(request, 'context-a')
        confirm = request.model_copy(update={'action': 'confirm', 'offer_id': offered.offer_id})
        self.assertEqual((await worker.negotiate(confirm, 'context-b')).status, 'declined')
        self.assertEqual((await worker.negotiate(confirm.model_copy(update={'query': 'Pick up a car'}), 'context-a')).status, 'declined')
        self.assertEqual((await worker.negotiate(confirm, 'context-a')).status, 'confirmed')
        self.assertEqual((await worker.negotiate(confirm, 'context-a')).status, 'confirmed')
        worker.offers[offered.offer_id].expires = time.monotonic() - 1
        self.assertEqual((await worker.negotiate(confirm, 'context-a')).status, 'declined')

    async def test_worker_cannot_offer_undeclared_capability(self):
        request = NegotiationRequest(action='propose', request_id='abc', query='Fly', required_capabilities=['flying'])
        reply = await self.workers['robot'].negotiate(request, 'context')
        self.assertEqual(reply.status, 'declined')
        self.assertEqual(self.workers['robot'].llm.calls, [])

    async def test_forged_confirmation_cannot_produce_found(self):
        original = self.peer.send
        async def send(card, data, context):
            reply = await original(card, data, context)
            if data.get('action') == 'confirm':
                reply['offer_id'] = 'wrong-offer'
            return reply
        self.peer.send = send
        self.manager_llm.script = all_cards() + [tool('propose_task', agent_url=ROBOT),
            tool('confirm_offer', agent_url=ROBOT), finish('not_found', None)]
        result = await self.manager.search(SearchRequest(query='Pick up a box', required_capabilities=['picking']))
        self.assertEqual(result.status, 'not_found')
        self.assertIn('different offer', result.attempts[-1].detail)

class ModelClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_tool_call_round_trip(self):
        requests = []

        def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            if len(requests) == 1:
                return httpx.Response(200, json={"choices": [{"message": {
                    "role": "assistant", "content": None, "reasoning": "private reasoning",
                    "tool_calls": [{"id": "native-call-1", "type": "function", "function": {
                        "name": "discover_agent", "arguments": {"agent_url": ROBOT}}}],
                }}]})
            self.assertEqual(payload["messages"][-1]["tool_call_id"], "native-call-1")
            self.assertEqual(payload["messages"][-1]["role"], "tool")
            return httpx.Response(200, json={"choices": [{"message": tool("ask_user", question="What should the robot do?")}]})

        from agents.manager_agent.manager_tools import TOOLS
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            model = OllamaJSON(http, "http://127.0.0.1:11435/v1", "gpt-oss:20b", "ollama", 30)
            messages = [{"role": "user", "content": "Find a robot"}]
            answer = await model.chat(messages, TOOLS)
            self.assertNotIn("reasoning", answer)
            self.assertEqual(json.loads(answer["tool_calls"][0]["function"]["arguments"]), {"agent_url": ROBOT})
            messages.extend([answer, {"role": "tool", "tool_call_id": "native-call-1", "content": "{}"}])
            await model.chat(messages, TOOLS)
        self.assertEqual(requests[0]["model"], "gpt-oss:20b")
        self.assertEqual(requests[0]["tools"], TOOLS)
        self.assertFalse(requests[0]["parallel_tool_calls"])
        self.assertNotIn("response_format", requests[0])

    async def test_malformed_tool_call_is_reported(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
            httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{"function": {}}]}}]}))) as http:
            model = OllamaJSON(http, "http://127.0.0.1:11435/v1", "gpt-oss:20b", "ollama", 30)
            with self.assertRaisesRegex(ModelError, "malformed tool call"):
                await model.chat([], [])

    async def test_json_schema_and_correct_endpoint(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"can_help":true,"reason":"OK"}'}}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            model = OllamaJSON(http, "http://127.0.0.1:11435/v1", "llama3.2:3b", "ollama", 30)
            answer = await model.generate("Assess", {}, Assessment)
        self.assertTrue(answer.can_help)
        self.assertEqual(str(requests[0].url), "http://127.0.0.1:11435/v1/chat/completions")
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["model"], "llama3.2:3b")
        self.assertEqual(payload["response_format"]["type"], "json_schema")

    async def test_model_not_found_keeps_diagnostic(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(404, json={"error": "model not found"}))) as http:
            model = OllamaJSON(http, "http://127.0.0.1:11435/v1", "missing", "ollama", 30)
            with self.assertRaisesRegex(ModelError, "HTTP 404.*model not found"):
                await model.generate("Assess", {}, Assessment)

    async def test_malformed_model_json_has_bounded_retry(self):
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "not JSON"}}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            model = OllamaJSON(http, "http://127.0.0.1:11435/v1", "test", "ollama", 30)
            with self.assertRaisesRegex(ModelError, "valid Assessment JSON"):
                await model.generate("Assess", {}, Assessment)
        self.assertEqual(len(calls), 2)


class ConfigurationTests(unittest.TestCase):
    def test_invalid_manager_budget_rejected(self):
        for variable in ("MANAGER_MAX_TURNS", "MANAGER_TIMEOUT_SECONDS"):
            with patch("agents.common.settings.load_dotenv"), patch.dict("os.environ", {variable: "0"}, clear=True):
                with self.assertRaisesRegex(ValueError, "must be positive"):
                    Settings.from_env()

    def test_missing_v1_rejected_early(self):
        with patch("agents.common.settings.load_dotenv"), patch.dict("os.environ", {"TOOL_LLM_URL": "http://127.0.0.1:11435"}):
            with self.assertRaisesRegex(ValueError, "must end in /v1"):
                Settings.from_env()

    def test_model_defaults_and_robot_skills(self):
        with patch("agents.common.settings.load_dotenv"), patch.dict("os.environ", {}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.subagent_model, "llama3.2:3b")
        self.assertEqual(settings.manager_model, "gpt-oss:20b")
        card = RobotAgent(FakeLLM(), url=settings.urls["robot"]).card
        self.assertEqual({s.id for s in card.skills}, {"picking", "mobility"})
        self.assertFalse(card.capabilities.streaming)


if __name__ == "__main__":
    unittest.main()
