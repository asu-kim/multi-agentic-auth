import logging
from uuid import uuid4
from typing import Any, Optional, List, Dict

import httpx, os, subprocess, shlex, json, base64, asyncio

from a2a.client import A2ACardResolver, A2AClient
from a2a.server.agent_execution import AgentExecutor, RequestContext
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

AGENT1_BASE_URL = "http://localhost:9999"    # The agent who has the session key ID in their card
SESSION_EXT_URI = "https://asu-kim.example/ext/sst-session-key/v1"

HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
CONFIG_PATH = "configs/net1/website.config"

def gen_hex_nonce_32():
    return secrets.token_hex(16)

def hmac_sha256_hex(key_bytes: bytes, msg_bytes: bytes) -> str:
    return hmac.new(key_bytes, msg_bytes, hashlib.sha256).hexdigest()

def _abs(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p))

def _parse_last_json_line(stdout: str) -> dict:
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise ValueError("No JSON line found in Node output")

def _fetch_session_keys_blocking(config_path: str, key_id: int) -> List[Dict[str, Any]]:
    here = os.path.dirname(os.path.abspath(__file__))
    agent_dir = _abs(os.path.join(here, "/Users/sunyoungkim/iotauth/entity/node/example_entities"))

    cmd = f"node website.js {shlex.quote(config_path)} keyId {int(key_id)}"
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

def _extract_first_text_from_context(context: RequestContext) -> str:
    if not context.message or not context.message.parts:
        return ""

    for part in context.message.parts:
        if hasattr(part, "text") and isinstance(part, TextPart):
            return part.text

        if hasattr(part, "root") and isinstance(part.root, TextPart):
            return part.root.text

    return ""


def _extract_session_key_id_from_card(card: AgentCard) -> int:
    card_json = card.model_dump(mode="python", exclude_none=True)
    caps = card_json.get("capabilities") or {}
    exts = caps.get("extensions") or []

    for ext in exts:
        if ext.get("uri") == SESSION_EXT_URI:
            params = ext.get("params") or {}
            if "sessionKeyId" not in params:
                raise KeyError("Found extension but sessionKeyId missing in params")
            return int(params["sessionKeyId"])

    raise KeyError(f"sessionKeyId not found in Agent1 card extensions for uri={SESSION_EXT_URI}")


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


async def _fetch_agent1_card(httpx_client: httpx.AsyncClient) -> AgentCard:
    resolver = A2ACardResolver(httpx_client=httpx_client, base_url=AGENT1_BASE_URL)

    logger.info(f"[Agent2] Fetch public card: {AGENT1_BASE_URL}{AGENT_CARD_WELL_KNOWN_PATH}")
    public_card = await resolver.get_agent_card()
    return public_card


async def _agent2_call_agent1(httpx_client: httpx.AsyncClient, agent1_card: AgentCard, user_text: str) -> str:
    client = A2AClient(httpx_client=httpx_client, agent_card=agent1_card)

    send_message_payload: dict[str, Any] = {
        "message": {
            "role": "user",
            "parts": [{"kind": "text", "text": user_text}],
            "messageId": uuid4().hex,
        }
    }
    request = SendMessageRequest(id=str(uuid4()), params=MessageSendParams(**send_message_payload))

    response = await client.send_message(request)
    return _extract_first_text_from_send_message_response(response) # response.root.result.parts[0].root.text 


class Agent2Executor(AgentExecutor):

    def __init__(self, card: Optional[AgentCard] = None):
        self._card = card
        self._pending: Dict[str, Dict[str, Any]] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_text = "verify the session key Id" # _extract_first_text_from_context(context)
        # TODO: get agent group from agent1's AgentCard
        task = context.message.parts[0].root.text
        task_parts = task.split()

        if task_parts[0] == "Hello1":
            session_key_id = int(task_parts[1])
            nonce1 = task_parts[2]
            try:
                keys = await asyncio.to_thread(_fetch_session_keys_blocking, CONFIG_PATH, session_key_id)
                session_key_value = keys[0]['cipherKey_b64']
            except Exception as e:
                await event_queue.enqueue_event(
                    new_agent_text_message(
                        f"Error in getting session key\n"
                        f"sessionKeyId={session_key_id}\n"
                        f"Failed to fetch session keys from Auth/KDS: {type(e).__name__}: {e}"
                    )
                )
                return

            hmac1 = hmac_sha256_hex(base64.b64decode(session_key_value), binascii.unhexlify(nonce1))
            nonce2 = gen_hex_nonce_32()
            hmac2 = hmac_sha256_hex(base64.b64decode(session_key_value), binascii.unhexlify(nonce2))
            self._pending["Handshake1"] = {
                "sessionKeyValue": session_key_value,
                "nonce2": nonce2,
                "hmac2": hmac2
            }
            response = f"Hello2 {hmac1} {nonce2}"
            await event_queue.enqueue_event(new_agent_text_message(response))

        elif task_parts[0] == "Hello3":
            hmac2_agent1 = task_parts[1]

            key, st = next(iter(self._pending.items()))
            session_key_value = st["sessionKeyValue"]
            nonce2 = st["nonce2"]
            hmac2 = st["hmac2"]
            
            ok = hmac.compare_digest(hmac2, hmac2_agent1)     
            if not ok:
                await event_queue.enqueue_event(
                    new_agent_text_message("[Agent2] Hmac2 values are different")
                )
                return
            await event_queue.enqueue_event(new_agent_text_message("HMAC2 verified"))
            self._pending.clear()
            return

        # If the task is not start with Hello1 or Hello3
        await event_queue.enqueue_event(new_agent_text_message("Agent2: unknown message"))  

        # try:
        #     async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as httpx_client:
        #         agent1_card = await _fetch_agent1_card(httpx_client)
        #         session_key_id = _extract_session_key_id_from_card(agent1_card)

        #         agent1_reply_text = await _agent2_call_agent1(
        #             httpx_client=httpx_client,
        #             agent1_card=agent1_card,
        #             user_text=user_text,
        #         )

        #     out = "\n".join([
        #         "[Agent2] Read sessionKeyId from Agent1 card:",
        #         f"sessionKeyId={session_key_id}",
        #         "",
        #         "[Agent2] Forwarded message to Agent1:",
        #         f"user_text={user_text}",
        #         "",
        #         "[Agent2] Agent1 replied:",
        #         agent1_reply_text,
        #     ])
            
        #     try:
        #         keys = await asyncio.to_thread(_fetch_session_keys_blocking, CONFIG_PATH, session_key_id)
        #         out += f"\ncipherKey_b64: {keys[0]['cipherKey_b64']}"
        #     except Exception as e:
        #         await event_queue.enqueue_event(
        #             new_agent_text_message(
        #                 f"Error in getting session key\n"
        #                 f"sessionKeyId={session_key_id}\n"
        #                 f"Failed to fetch session keys from Auth/KDS: {type(e).__name__}: {e}"
        #             )
        #         )
        #         return

        #     await event_queue.enqueue_event(new_agent_text_message(out))

        # except Exception as e:
        #     await event_queue.enqueue_event(
        #         new_agent_text_message(f"[Agent2] Error while talking to Agent1: {type(e).__name__}: {e}")
        #     )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")
