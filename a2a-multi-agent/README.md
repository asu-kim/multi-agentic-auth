# A2A capability discovery team

One LangGraph manager discovers three independent agents from their **A2A Agent Cards**, negotiates suitability, and reports `found` or `not_found` with the negotiation history. All agent-to-agent requests use A2A 0.3 JSON-RPC through the official Python SDK's `ClientFactory` (not deprecated `A2AClient`). Model inference uses your local Ollama OpenAI-compatible API.

| Agent | Model | Port | Advertised skill IDs |
| --- | --- | --- | --- |
| Manager | `gpt-oss:20b` | 10000 | `agent_discovery` |
| Language | `llama3.2:3b` | 10001 | `translation`, `summarization` |
| Analytics | `llama3.2:3b` | 10002 | `statistics`, `anomaly_detection` |
| Robot | `llama3.2:3b` | 10003 | `picking`, `mobility` |

Language and Analytics are the two arbitrarily chosen sample agents. Their abilities are stable across restarts so the examples are reproducible. All three sub-agents share the same Ollama model, but have separate server processes, cards, prompts, and offer stores.

The robot is a **negotiation simulation**. It advertises picking up to 2 kg and movement on flat indoor floors; no physical robot commands are sent. The other sample agents also negotiate capability rather than execute translation or analysis jobs. This project finds one suitable agent; it does not split a request across a team.

## Agent folder structure

Each named folder is an independent agent, with its own class, A2A executor,
startup module, and stored JSON card. The three sub-agents inherit common
negotiation behavior but have separate instances, offer stores, and servers.

```text
agents/
├── manager_agent/
│   ├── __main__.py                 # Start the manager A2A server
│   ├── manager_agent.py            # LangGraph discovery and negotiation
│   ├── manager_agent_executor.py   # Handle user A2A requests
│   └── manager_agent_card.json
├── language_agent/
│   ├── __main__.py
│   ├── language_agent.py
│   ├── language_agent_executor.py
│   └── language_agent_card.json    # Translation and summarization
├── analytics_agent/
│   ├── __main__.py
│   ├── analytics_agent.py
│   ├── analytics_agent_executor.py
│   └── analytics_agent_card.json   # Statistics and anomaly detection
├── robot_agent/
│   ├── __main__.py
│   ├── robot_agent.py
│   ├── robot_agent_executor.py
│   └── robot_agent_card.json       # Picking and mobility
├── common/
│   ├── agent_card_loader.py        # Read a card; no capability definitions
│   ├── negotiating_agent.py       # Shared sub-agent negotiation behavior
│   ├── negotiation_executor.py    # Shared A2A negotiation adapter
│   ├── negotiation_models.py      # Request and response schemas
│   ├── a2a_client.py
│   ├── ollama_client.py
│   ├── server_runtime.py
│   └── settings.py
├── client.py              # Send a request to the manager
├── check_ollama_running.py
└── run_all.py                      # Start all four processes
```

Edit an agent's adjacent `*_agent_card.json` to change its name, skills,
examples, or limits (in `description`). The same card is used for its own
negotiation checks and published through A2A. Restart the agent after editing.
Configured URLs override the default `url` in the JSON; model choices remain
in `.env`. There is no shared card catalog or card-generation function.
The manager learns other agents' abilities over A2A, not by reading their files.

## Run on your Linux machine (no sudo or uv)

Copy/extract this folder to `~/multi-agentic-auth/a2a-multi-agent`. Use Python 3.12+ virtual environment:

```bash
cd ~/multi-agentic-auth/a2a-multi-agent
source ~/venv/bin/activate
python --version
python -m pip install -e .
cp .env.example .env
```

Copy `.env.example` only during initial setup; preserve your edits on subsequent runs. The defaults already use your Ollama server at **`127.0.0.1:11435`**:

```dotenv
TOOL_LLM_URL=http://127.0.0.1:11435/v1
API_KEY=ollama
MANAGER_MODEL=gpt-oss:20b
SUBAGENT_MODEL=llama3.2:3b
```

Check that both models are present on that same Ollama instance:

```bash
export OLLAMA_HOST=127.0.0.1:11435
ollama list
ollama pull llama3.2:3b
ollama pull gpt-oss:20b
python -m agents.check_ollama_running
```

Your existing Ollama server must stay running. You can exit an `ollama run` chat with `/bye`; the interactive chat is not required. `llama3.2:3b` is the actual Ollama 3B Llama tag. Change `SUBAGENT_MODEL` if you have a custom model alias.

Stop the old demo agent if it occupies port 10000, then start the complete team:

```bash
python -m agents.run_all
```

Wait for all four servers to report startup complete. Ctrl+C stops the team. If one server exits, the launcher stops the others. Alternatively, run each in its own terminal with the same virtual environment:

```bash
python -m agents.language_agent
python -m agents.analytics_agent
python -m agents.robot_agent
python -m agents.manager_agent
```


Those are four separate commands for four separate terminals, not a sequential shell script. To use other ports, change `MANAGER_URL`, `LANGUAGE_URL`, `ANALYTICS_URL`, and `ROBOT_URL` in `.env`. Server ports are read from those URLs. Restart affected processes after configuration changes. Exported shell variables take precedence over `.env`; unset an old `TOOL_LLM_URL` export if your edits appear to be ignored.

## Ask the manager

In a second terminal, activate your virtual environment and enter the project folder.

```bash
python -m agents.client \
  "Find an agent that can pick up a 1 kg box and move it to station B on a flat indoor floor." --json
```

Expected successful result (IDs and reasoning vary):

```json
{
  "status": "found",
  "required_capabilities": ["picking", "mobility"],
  "selected_agent": {
    "name": "Robot Agent",
    "url": "http://127.0.0.1:10003/",
    "capabilities": ["picking", "mobility"],
    "offer_id": "..."
  },
  "summary": "Found Robot Agent at http://127.0.0.1:10003/. It confirmed: picking, mobility."
}
```

The actual response also includes the original query, discovered agents, and proposal/confirmation attempts. For explicit capability matching without LLM interpretation of the request, add skill IDs:

```bash
python -m agents.client \
  "Pick up a 1 kg box and carry it indoors to station B." \
  --capability picking --capability mobility --json

python -m agents.client \
  "Find an agent to translate written English text to Spanish." --json

python -m agents.client \
  "Find an agent to detect anomalies in supplied sensor readings." --json

python -m agents.client \
  "Find an agent that can fly." --capability flying --json

python -m agents.client \
  "Pick up a 20 kg box." --capability picking --json

python -m agents.client \
  "Find one agent that translates text and picks up boxes." \
  --capability translation --capability picking --json
```

Expected outcomes: translation → Language; anomaly detection → Analytics; flying → `not_found`; 20 kg pickup → Robot should decline after its LLM assessment; translation plus picking → `not_found`, because no single agent advertises both. Natural-language extraction and feasibility reasoning depend on the models. Explicit `--capability` IDs bypass manager extraction, but sub-agents still use their LLM to assess the proposal.

Set `ROBOT_AVAILABLE=false` and restart the robot to demonstrate a matching card followed by a negotiation decline. Stop an agent to demonstrate a partial discovery failure.

Client exit codes: `0` for found; `1` for a structured non-success result; `2` for the handled HTTP/configuration errors. The JSON `status` distinguishes `not_found`, `input_required`, and `error`. An unavailable peer is recorded in `attempts`; a result never claims an exhaustive search beyond the configured roster.

## Discovery and negotiation

```mermaid
sequenceDiagram
    participant U as User client
    participant M as Manager (gpt-oss:20b)
    participant A as Three A2A sub-agents
    participant R as Matching agent (llama3.2:3b)
    U->>M: A2A message/send: desired task
    M->>A: GET /.well-known/agent-card.json
    A-->>M: Skills and descriptions
    M->>M: Extract all required skills; filter cards
    M->>R: A2A message/send: propose
    R->>R: LLM assesses task against capabilities and limits
    R-->>M: Offer + offer ID, or decline
    M->>R: A2A message/send: confirm offer
    R-->>M: Confirmed, or decline if stale/unavailable
    M-->>U: found / not_found, selected agent and attempts
```

The manager's graph is `discover → interpret → negotiate → report`. A vague query goes directly from interpretation to a clarification result. Unmatched requests have no candidate to negotiate with and return `not_found` after card comparison. Multiple matching candidates are tried in roster order until one confirms. A declined or failed candidate does not prevent trying the next.

The manager is configured with **addresses only**, not a hardcoded capability routing table. `AGENT_URLS` can supply a comma-separated list of extra agent addresses. Each card exposes abilities in **`skills`**. The A2A `capabilities` field describes transport features such as streaming; it is not the field for robot abilities. This demo advertises non-streaming support and uses synchronous `message/send` exchanges.

Negotiation is this application's convention carried inside standard A2A **DataPart** messages, not an additional A2A RPC method. To add an external agent, it must implement the `capability-negotiation/v1` envelope in `common/negotiation_models.py`, in addition to publishing an A2A card. Merely publishing any A2A card is insufficient to negotiate with this demo.

Proposal example:

```json
{
  "protocol": "capability-negotiation/v1",
  "action": "propose",
  "request_id": "a-unique-request-id",
  "query": "Pick up a 1 kg box and carry it indoors.",
  "required_capabilities": ["picking", "mobility"],
  "offer_id": null
}
```

Confirmation repeats the same request ID, query, and capability list, changes `action` to `confirm`, and supplies the returned `offer_id`. Both rounds use the same A2A `contextId` and different `messageId` values. Offers expire after ten minutes and are kept in memory. Confirmations are idempotent within that lifetime. They confirm suitability only, not a reservation or executed task. A server restart discards offers.

The manager validates request IDs, agent names, offered skills, and the confirmed offer ID before returning `found`. An LLM cannot add capabilities outside a worker's declared profile. Free-text feasibility limits are assessed by the worker LLM; physical deployments need independent hardware checks and a separate execution step.

## Inspect cards and diagnose configuration

```bash
curl http://127.0.0.1:10003/.well-known/agent-card.json
curl http://127.0.0.1:11435/v1/models
```

- Missing model: pull it into the same Ollama server selected by `OLLAMA_HOST`.
- `404 page not found`: the inference base URL must include `/v1`; startup validates this.
- Agent cannot be reached: check that all four processes are running and ports are free.
- Slow first response: Ollama may need to load/switch the 20B and 3B models. `LLM_TIMEOUT_SECONDS` defaults to 180 and `A2A_TIMEOUT_SECONDS` to 900; increase these for slow hardware or many candidates. The model check lists installed models but does not verify memory capacity or inference speed.
- Model errors are included in the result and server traceback, rather than hidden behind a generic A2A internal error.

Local HTTP clients ignore proxy environment variables. For agents on other hosts, configure their reachable advertised URLs and start the servers with `--host 0.0.0.0`. This demo has no authentication or IoTAuth integration and is intended for local development.

## Tests and source

```bash
python -m unittest discover -s tests -v
```

Tests exercise actual A2A SDK card discovery and JSON-RPC messaging via ASGI HTTP transports, the LangGraph manager, offers and confirmations, unavailable agents, incorrect confirmations, unsupported capabilities, and inference errors. Model outputs are deterministic test doubles; these tests do not download models or validate real model reasoning.

See the folder map above. Start with `manager_agent/manager_agent.py` for the
manager's graph, and with each sub-agent's `*_agent.py` and `*_agent_card.json`
for its identity and abilities. Its `*_agent_executor.py` adapts A2A messages,
while its `__main__.py` wires up the model and starts its server.

References: [A2A 0.3 specification](https://a2a-protocol.org/v0.3.0/specification/), [Ollama API compatibility](https://docs.ollama.com/api/openai-compatibility), [Llama 3.2 model tags](https://ollama.com/library/llama3.2).
