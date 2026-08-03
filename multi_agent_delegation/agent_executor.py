import logging
from uuid import uuid4
from typing import Any, Dict, List, Optional

import httpx, os, subprocess, shlex, json, base64, asyncio

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.client import A2ACardResolver, A2AClient
from a2a.server.events import EventQueue
from a2a.types import (
    AgentCard,
    MessageSendParams,
    SendMessageRequest,
    SendMessageSuccessResponse,
    TextPart,
)
from a2a.utils import new_agent_text_message
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
import hmac, hashlib, secrets, binascii

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

SESSION_EXT_URI = "https://asu-kim.example/ext/sst-session-key/v1"
AGENT2_BASE_URL = "http://localhost:9998" 

HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
CONFIG_PATH = "configs/net1/lowTrustAgent.config"

LLM_MODEL = "gpt-oss:20b"
LLM_URL = "http://localhost:11434"


def gen_hex_nonce_32():
    return secrets.token_hex(16)

def hmac_sha256_hex(key_bytes: bytes, msg_bytes: bytes) -> str:
    return hmac.new(key_bytes, msg_bytes, hashlib.sha256).hexdigest()

def _parse_last_json_line(stdout: str) -> dict:
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise ValueError("No JSON line found in Node output")

def _fetch_session_keys_blocking(config_path: str, key_id: int) -> List[Dict[str, Any]]:
    here = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.environ.get('MAA_ROOT', os.path.dirname(here)) 
    agent_dir = os.path.abspath(os.path.join(root_dir, "iotauth/entity/node/example_entities"))

    cmd = f"node agent.js {shlex.quote(config_path)} keyId {int(key_id)}"
    p = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=agent_dir,
    )
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip() or "node error")

    data = _parse_last_json_line(p.stdout)

    session_key_value = []
    for k in data.get("session_keys", []):
        session_key_value.append({
            "id": int(k["id"]),
            "cipherKey_b64": k["cipherKey_b64"],
            "macKey_bytes": base64.b64decode(k["macKey_b64"]),
            "absValidity": k.get("absValidity"),
            "relValidity": k.get("relValidity"),
        })

    if not session_key_value:
        raise ValueError("Empty session_keys in JSON")

    return session_key_value

# def _get_session_key_id_from_card(card) -> Optional[int]:
#     caps = getattr(card, "capabilities", None)
#     if not caps:
#         return None

#     exts = getattr(caps, "extensions", None) or []
#     for ext in exts:
#         if isinstance(ext, dict):
#             uri = ext.get("uri")
#             params = ext.get("params") or {}
#         else:
#             uri = getattr(ext, "uri", None)
#             params = getattr(ext, "params", None) or {}

#         if uri == SESSION_EXT_URI:
#             if "sessionKeyId" not in params:
#                 return None
#             return int(params["sessionKeyId"])

#     return None

def _extract_first_text_from_send_message_response(resp: Any) -> str:
    root = getattr(resp, "root", resp)

    if isinstance(root, SendMessageSuccessResponse):
        message = root.result
        for part in message.parts:
            if hasattr(part, "root") and isinstance(part.root, TextPart):
                return part.root.text
        return "(Agent1 responded but no TextPart found)"
    else:
        try:
            return f"(Non-success response) {root.model_dump(mode='json', exclude_none=True)}"
        except Exception:
            return f"(Non-success response) {str(root)}"

def _extract_task_text_from_context(context: RequestContext) -> str:
    if not context.message or not context.message.parts:
        return ""

    for part in context.message.parts:
        if hasattr(part, "text") and isinstance(part, TextPart):
            return part.text

        if hasattr(part, "root") and isinstance(part.root, TextPart):
            return part.root.text

    return ""

async def agent1_call_agent2(httpx_client: httpx.AsyncClient, agent2_card: AgentCard, user_text: str) -> str:
    client = A2AClient(httpx_client=httpx_client, agent_card=agent2_card)

    send_message_payload: dict[str, Any] = {
        "message": {
            "role": "user",
            "parts": [{"kind": "text", "text": user_text}],
            "messageId": uuid4().hex,
        }
    }
    request = SendMessageRequest(id=str(uuid4()), params=MessageSendParams(**send_message_payload))
    response = await client.send_message(request)
    return _extract_first_text_from_send_message_response(response)

async def _fetch_agent2_card(httpx_client: httpx.AsyncClient) -> AgentCard:
    resolver = A2ACardResolver(httpx_client=httpx_client, base_url=AGENT2_BASE_URL)
    logger.info(f"[Agent2] Fetch public card: {AGENT2_BASE_URL}{AGENT_CARD_WELL_KNOWN_PATH}")
    public_card = await resolver.get_agent_card()
    return public_card

class Agent1:  
    # async def invoke(self) -> str:
    #     return "Hello World"
    async def llm_decide(state: dict, incoming: str) -> dict:
        payload = {
            "model": LLM_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Choose exactly one action from the allowed list.\n"
                        "After connection verification, finish the handshake process."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "role": "Agent1",
                        "state": state,
                        "incoming_message": incoming,
                        "allowed_actions": [
                            "SEND_HELLO1",
                            "VERIFY_HELLO2_SEND_HELLO3",
                            "ABORT",
                            "FINISHED",
                        ],
                    }),
                },
            ],
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{LLM_URL}/api/chat", json=payload)
            r.raise_for_status()
            return json.loads(r.json()["message"]["content"])


class Agent1Executor(AgentExecutor):
    "An agent who has session Key Id in their AgentCard"
    def __init__(self, card: AgentCard):
        self.agent = Agent1()
        self._card = card
        self.state = {
            "phase": "INIT",
            "sessionKeyId": None,
            "nonce1": None,
        }

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:   
        incoming = _extract_task_text_from_context(context)


        session_key_id = int(incoming.split()[0])

        try:
            keys = await asyncio.to_thread(_fetch_session_keys_blocking, CONFIG_PATH, session_key_id)
        except Exception as e:
            await event_queue.enqueue_event(
                new_agent_text_message(
                    f"Hello World\n"
                    f"sessionKeyId={session_key_id}\n"
                    f"Failed to fetch session keys from Auth/KDS: {type(e).__name__}: {e}"
                )
            )
            return
        
        session_key_value = keys[0]['cipherKey_b64']
        nonce1_from_agent1 = gen_hex_nonce_32()
        hmac1_agent1 = hmac_sha256_hex(base64.b64decode(session_key_value), binascii.unhexlify(nonce1_from_agent1))

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as httpx_client:
                agent2_card = await _fetch_agent2_card(httpx_client)

                agent2_reply_text = await agent1_call_agent2(
                    httpx_client=httpx_client,
                    agent2_card=agent2_card,
                    user_text=f"Hello1 {session_key_id} {nonce1_from_agent1}",
                )
        except Exception as e:
            await event_queue.enqueue_event(
                new_agent_text_message(f"[Agent1] Error while talking to Agent2 in phase1: {type(e).__name__}: {e}")
            )

        agent2_reply_text_parts = agent2_reply_text.split()
        if agent2_reply_text_parts[0] != "Hello2":
            await event_queue.enqueue_event(
                new_agent_text_message(f"[Agent1] Incorrect handshake2 reply {agent2_reply_text}")
            )
            return
        
        hmac1_agent2 = agent2_reply_text_parts[1]
        nonce2_from_agent2 = agent2_reply_text_parts[2]
        
        ok = hmac.compare_digest(hmac1_agent1, hmac1_agent2) 
        if not ok:
            await event_queue.enqueue_event(
                new_agent_text_message(f"[Agent1] Hmac1 values are different")
            )
            return
        
        hmac2_agent1 = hmac_sha256_hex(base64.b64decode(session_key_value), binascii.unhexlify(nonce2_from_agent2))
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as httpx_client:
                agent2_card = await _fetch_agent2_card(httpx_client)

                agent2_reply_text2 = await agent1_call_agent2(
                    httpx_client=httpx_client,
                    agent2_card=agent2_card,
                    user_text=f"Hello3 {hmac2_agent1}",
                )
        except Exception as e:
            await event_queue.enqueue_event(
                new_agent_text_message(f"[Agent1] Error while talking to Agent2 in phase2: {type(e).__name__}: {e}")
            )
        
        
        result_verifying_hmac2 = agent2_reply_text2.strip()
        if result_verifying_hmac2 != "HMAC2 verified":
            await event_queue.enqueue_event(
                new_agent_text_message(f"[Agent1] Hmac2 values are different {result_verifying_hmac2}")
            )
            return
        
        out = "\n".join([
            "[Agent1] handshake complete",
            f"sessionKeyId={session_key_id}",
            f"nonce1={nonce1_from_agent1}",
            f"nonce2={nonce2_from_agent2}",
            f"verify_hmac1_ok={ok}",
            f"agent2_verify_reply={result_verifying_hmac2}",
        ])
        await event_queue.enqueue_event(new_agent_text_message(out))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")
