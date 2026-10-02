import json
import time
import unittest
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
from agents.common.negotiation_models import Assessment, NegotiationRequest, SearchPlan, SearchRequest, SearchResult
from agents.common.a2a_client import A2APeer


class FakeLLM:
    """Only test model inference is stubbed; A2A uses the real SDK and HTTP stack."""

    def __init__(self, answer=None, error=None):
        self.answer = answer or Assessment(can_help=True, reason="Within my advertised capabilities and limits.")
        self.error = error
        self.calls = []

    async def generate(self, system, payload, schema):
        self.calls.append((payload, schema))
        if self.error:
            raise self.error
        return self.answer


class Network(httpx.AsyncBaseTransport):
    """Route HTTP requests to independent ASGI A2A apps without binding test ports."""

    def __init__(self):
        self.apps = {}
        self.requests = []

    async def handle_async_request(self, request):
        self.requests.append((str(request.url), request.method))
        app = self.apps.get(request.url.port)
        if app is None:
            raise httpx.ConnectError("Agent offline", request=request)
        return await httpx.ASGITransport(app=app).handle_async_request(request)


class TeamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.network = Network()
        self.http = httpx.AsyncClient(transport=self.network)
        self.peer = A2APeer(self.http)
        self.urls = tuple(f"http://127.0.0.1:{port}/" for port in (10001, 10002, 10003))
        self.workers = {}
        worker_types = {"language": (LanguageAgent, build_language_app),
                        "analytics": (AnalyticsAgent, build_analytics_app),
                        "robot": (RobotAgent, build_robot_app)}
        for role, url in zip(("language", "analytics", "robot"), self.urls):
            agent_class, build_app = worker_types[role]
            worker = agent_class(FakeLLM(), url=url)
            self.workers[role] = worker
            self.network.apps[httpx.URL(url).port] = build_app(worker)
        self.manager_llm = FakeLLM(SearchPlan(required_capabilities=["picking", "mobility"], clarification=None))
        self.manager = ManagerAgent(self.urls, self.peer, self.manager_llm)
        self.network.apps[10000] = build_manager_app(self.manager)

    async def asyncTearDown(self):
        await self.http.aclose()

    async def ask(self, query, required=None):
        card = await self.peer.discover("http://127.0.0.1:10000/")
        data = SearchRequest(query=query, required_capabilities=required or []).model_dump()
        return SearchResult.model_validate(await self.peer.send(card, data, "user-context"))

    async def test_robot_found_through_real_a2a_and_langgraph(self):
        result = await self.ask("Find an agent to pick up a 1 kg box and carry it indoors.")
        self.assertEqual(result.status, "found")
        self.assertEqual(result.selected_agent.name, "Robot Agent")
        self.assertEqual([a.status for a in result.attempts], ["offered", "confirmed"])
        self.assertEqual(len(self.manager_llm.calls), 1)
        self.assertEqual(len(self.workers["robot"].llm.calls), 1)
        self.assertTrue(any("agent-card.json" in url for url, _ in self.network.requests))
        posts = [url for url, method in self.network.requests if method == "POST"]
        self.assertEqual(posts.count("http://127.0.0.1:10003/"), 2)

    async def test_both_sample_agents_can_be_found(self):
        for capability, name in [("translation", "Language Agent"), ("anomaly_detection", "Analytics Agent")]:
            result = await self.ask("Find a specialist", [capability])
            self.assertEqual(result.status, "found")
            self.assertEqual(result.selected_agent.name, name)

    async def test_unknown_capability_is_not_found(self):
        result = await self.ask("Find a flying agent", ["flying"])
        self.assertEqual(result.status, "not_found")
        self.assertIsNone(result.selected_agent)
        self.assertEqual(result.attempts, [])

    async def test_partial_matches_do_not_count_as_one_agent(self):
        result = await self.ask("Pick up a box and translate a document", ["picking", "translation"])
        self.assertEqual(result.status, "not_found")
        self.assertIsNone(result.selected_agent)

    async def test_natural_language_unsupported_requirement_preserved(self):
        self.manager_llm.answer = SearchPlan(required_capabilities=["picking", "flying"], clarification=None)
        result = await self.ask("Find a robot to pick up a parcel and fly it across town")
        self.assertEqual(result.required_capabilities, ["picking", "flying"])
        self.assertEqual(result.status, "not_found")

    async def test_worker_declines_infeasible_task_after_model_assessment(self):
        self.workers["robot"].llm.answer = Assessment(can_help=False, reason="20 kg exceeds the 2 kg payload limit.")
        result = await self.ask("Pick up a 20 kg box", ["picking"])
        self.assertEqual(result.status, "not_found")
        self.assertEqual(result.attempts[0].status, "declined")
        self.assertIn("20 kg", result.attempts[0].detail)

    async def test_unavailable_agent_declines(self):
        self.workers["robot"].available = False
        result = await self.ask("Pick up a box", ["picking"])
        self.assertEqual(result.status, "not_found")
        self.assertIn("unavailable", result.attempts[0].detail)

    async def test_offline_agent_does_not_prevent_other_match(self):
        del self.network.apps[10001]
        result = await self.ask("Pick up a box", ["picking"])
        self.assertEqual(result.status, "found")
        self.assertEqual(result.attempts[0].phase, "discovery")
        self.assertEqual(result.attempts[0].status, "error")

    async def test_offline_robot_report_is_incomplete_not_false_certainty(self):
        del self.network.apps[10003]
        result = await self.ask("Pick up a box", ["picking"])
        self.assertEqual(result.status, "not_found")
        self.assertIn("incomplete", result.summary)

    async def test_model_failure_is_reported_with_reason(self):
        self.workers["robot"].llm.error = ModelError("model 'llama3.2:3b' not found")
        result = await self.ask("Pick up a box", ["picking"])
        self.assertEqual(result.status, "not_found")
        self.assertIn("llama3.2:3b", result.attempts[0].detail)
        self.assertIn("incomplete", result.summary)

    async def test_manager_model_failure_is_error_not_not_found(self):
        self.manager_llm.error = ModelError("manager model offline")
        result = await self.ask("Find a robot")
        self.assertEqual(result.status, "error")
        self.assertIn("manager model offline", result.summary)

    async def test_vague_request_requires_input(self):
        self.manager_llm.answer = SearchPlan(required_capabilities=[], clarification="Which ability do you need?")
        result = await self.ask("Find someone")
        self.assertEqual(result.status, "input_required")
        self.assertEqual(result.attempts, [])

    async def test_confirmation_requires_matching_offer_and_context(self):
        worker = self.workers["robot"]
        request = NegotiationRequest(action="propose", request_id="abc", query="Pick up a 1 kg box",
                                     required_capabilities=["picking"])
        offered = await worker.negotiate(request, "context-a")
        confirm = request.model_copy(update={"action": "confirm", "offer_id": offered.offer_id})
        wrong_context = await worker.negotiate(confirm, "context-b")
        self.assertEqual(wrong_context.status, "declined")
        changed = await worker.negotiate(confirm.model_copy(update={"query": "Pick up a car"}), "context-a")
        self.assertEqual(changed.status, "declined")
        success = await worker.negotiate(confirm, "context-a")
        self.assertEqual(success.status, "confirmed")
        repeat = await worker.negotiate(confirm, "context-a")
        self.assertEqual(repeat.status, "confirmed")
        worker.offers[offered.offer_id].expires = time.monotonic() - 1
        expired = await worker.negotiate(confirm, "context-a")
        self.assertEqual(expired.status, "declined")

    async def test_worker_cannot_offer_undeclared_capability(self):
        request = NegotiationRequest(action="propose", request_id="abc", query="Fly",
                                     required_capabilities=["flying"])
        reply = await self.workers["robot"].negotiate(request, "context")
        self.assertEqual(reply.status, "declined")
        self.assertEqual(self.workers["robot"].llm.calls, [])

    async def test_forged_confirmation_cannot_produce_found(self):
        original = self.peer.send

        async def send(card, data, context):
            reply = await original(card, data, context)
            if data.get("action") == "confirm":
                reply["offer_id"] = "wrong-offer"
            return reply

        self.peer.send = send
        result = await self.manager.search(SearchRequest(query="Pick up a box", required_capabilities=["picking"]))
        self.assertEqual(result.status, "not_found")
        self.assertIn("different offer", result.attempts[-1].detail)


class ModelClientTests(unittest.IsolatedAsyncioTestCase):
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
