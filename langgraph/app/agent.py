from collections.abc import AsyncIterable
from typing import Any, Dict, List, Optional, Literal

import httpx, os, subprocess, shlex, json, base64, asyncio
import hmac, hashlib, secrets, binascii, threading, queue

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel


memory = MemorySaver()

CONFIG_PATH = "configs/net1/client.config"


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
    agent_dir = os.path.abspath(os.path.join(root_dir, "../../iotauth/entity/node/example_entities"))

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


@tool
async def connect_server(timeout: int = 60) -> str:
    """..."""
    return await asyncio.to_thread(connect_server_blcok, timeout)


def connect_server_blcok(timeout: int = 60) -> str:
    """Start the Node.js client (client.js), send initComm, and verify the
    handshake succeeded by checking for 'switching to IN_COMM' in stdout.

    Args:
        timeout: seconds to wait for the success marker before giving up.

    Returns:
        A success or failure message including relevant stdout/stderr output.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.environ.get('MAA_ROOT', os.path.dirname(here))
    agent_dir = os.path.abspath(os.path.join(root_dir, "../../iotauth/entity/node/example_entities"))

    proc = subprocess.Popen(
        ["node", "client.js"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=agent_dir,
        text=True,
        bufsize=1,  # line-buffered
    )

    q = queue.Queue()

    def reader():
        for line in proc.stdout:
            q.put(line)
        q.put(None)

    threading.Thread(target=reader, daemon=True).start()

    output_lines = []
    saw_params_block = False
    sent_init = False
    success = False

    try:
        while True:
            try:
                line = q.get(timeout=timeout)
            except queue.Empty:
                break

            if line is None:  # stdout closed
                break

            output_lines.append(line)

            if "current parameters:" in line:
                saw_params_block = True

            if saw_params_block and not sent_init and line.strip() == "}":
                try:
                    proc.stdin.write("initComm\n")
                    proc.stdin.flush()
                    sent_init = True
                except BrokenPipeError:
                    proc.kill()
                    return "Failed: client.js exited before initComm could be sent."

            if "switching to IN_COMM" in line:
                success = True
                break

        if success:
            return f"Success: reached IN_COMM state.\n---\n{''.join(output_lines)}"
        else:
            stderr_output = proc.stderr.read() if proc.stderr else ""
            if proc.poll() is None:
                proc.terminate()
            return (
                f"Failed: did not observe 'switching to IN_COMM' within {timeout}s "
                f"(initComm sent: {sent_init}).\n"
                f"stdout:\n{''.join(output_lines)}\nstderr:\n{stderr_output}"
            )
    finally:
        # client.js must always be terminated, regardless of success, failure, or exception.
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass


def _extract_text_from_parts(content) -> str:
    """Given an AIMessage.content value, return only the text portions.

    content is usually a plain string, but some models return a list of
    content blocks/parts instead, e.g.:
        [{'type': 'text', 'text': 'Connection established...'}, {'type': 'tool_use', ...}]
        [{'kind': 'text', 'text': 'Connection established...'}]

    This pulls out every 'text' value found (in order) and joins them,
    ignoring any non-text parts (tool calls, images, etc.).
    """
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict) and part.get('text'):
                texts.append(str(part['text']))
            elif isinstance(part, str) and part.strip():
                texts.append(part)
        return "\n".join(t.strip() for t in texts if t.strip())

    return ""


class ManagerAgent:
    """ManagerAgent - a assistant who works on behalf of human user."""

    SYSTEM_INSTRUCTION = (
        "Your sole purpose is to use the 'connect_server' tool to establish and verify the connection. "
        'If the user asks about anything other than connecting to the server, '
        'politely state that you cannot help with that topic and can only assist with server connection requests. '
        'Do not attempt to answer unrelated questions or use tools for other purposes.'
    )

    FORMAT_INSTRUCTION = (
        'Set response status to input_required if the user needs to provide more information to complete the request.'
        'Set response status to error if there is an error while processing the request.'
        'Set response status to completed if the request is complete.'
    )

    def __init__(self):
        model_source = os.getenv('model_source', 'google')
        if model_source == 'google':
            self.model = ChatGoogleGenerativeAI(model='gemini-2.0-flash')
        else:
            self.model = ChatOpenAI(
                model=os.getenv('TOOL_LLM_NAME'),
                openai_api_key=os.getenv('API_KEY', 'EMPTY'),
                openai_api_base=os.getenv('TOOL_LLM_URL'),
                temperature=0,
            )
        self.tools = [connect_server]

        self.graph = create_react_agent(
            self.model,
            tools=self.tools,
            checkpointer=memory,
            prompt=self.SYSTEM_INSTRUCTION,
        )

    async def stream(self, query, context_id) -> AsyncIterable[dict[str, Any]]:
        inputs = {'messages': [('user', query)]}
        config = {'configurable': {'thread_id': context_id}}
        final_message = None

        async for item in self.graph.astream(inputs, config, stream_mode='values'):
            message = item['messages'][-1]
            final_message = message

            if (
                    isinstance(message, AIMessage)
                    and message.tool_calls
                    and len(message.tool_calls) > 0
            ):
                yield {
                    'is_task_complete': False,
                    'require_user_input': False,
                    'content': 'Connecting to the server...',
                }
            elif isinstance(message, ToolMessage):
                yield {
                    'is_task_complete': False,
                    'require_user_input': False,
                    'content': 'Verifying the server connection...',
                }

        # No structured-output pass is required. The final AI message is the result.
        current_state = self.graph.get_state(config)
        messages = current_state.values.get('messages', [])
        if messages:
            final_message = messages[-1]

        if isinstance(final_message, AIMessage):
            content = final_message.content
            # Print the full raw content first (the "log"), then pull out
            # only the text portions from it below.
            print(f"DEBUG full final message content: {content!r}")

            text = _extract_text_from_parts(content)
            if text:
                yield {
                    'is_task_complete': True,
                    'require_user_input': False,
                    'content': text,
                }
                return

        yield {
            'is_task_complete': False,
            'require_user_input': True,
            'content': 'Unable to complete the server connection request.',
        }

    SUPPORTED_CONTENT_TYPES = ['text', 'text/plain']
