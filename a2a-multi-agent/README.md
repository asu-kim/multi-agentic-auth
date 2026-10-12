# A2A card-based agent selection

An autonomous LLM manager reads independent agents' **A2A Agent Cards**, compares
skills and declared limits with the user's request, and selects **one** suitable
agent. There is no proposal/confirmation negotiation, task reservation, or automatic task execution during selection. A `found` result means the card appears suitable;
it does not confirm current availability or actual ability to execute the task.

| Agent | Model | Port | Advertised skill IDs |
| --- | --- | --- | --- |
| Manager | `gpt-oss:20b` | 10000 | `agent_discovery` |
| Language | `llama3.2:3b` | 10001 | `translation`, `summarization` |
| Analytics | `llama3.2:3b` | 10002 | `statistics`, `anomaly_detection` |
| Robot | `llama3.2:3b` | 10003 | `picking`, `mobility` |

Each agent is a separate server process with its own stored JSON card. The manager
knows configured addresses only; it learns abilities by fetching cards over HTTP
using the A2A SDK's card resolver. User requests to the manager still use A2A 0.3
JSON-RPC `message/send` through `ClientFactory`. The manager never sends task
messages to the workers. All four agents have their own Ollama client. Worker inference runs only for explicit task requests; card discovery never calls a worker model.

## Decision loop

```text
User -> A2A -> Manager LLM
                 |
                 +-> discover_agent: fetch an A2A card
                 +-> set_requirements: interpret all required skills
                 +-> assess_agent: record the manager's own card-based judgment
                 +-> finish_search: select an agent or report no suitable card
                 +-> ask_user: request missing details
```

The LLM chooses actions and discovery order; this is not a fixed workflow. It can
inspect Robot first and finish early, compare several cards, or retry a failed
lookup. `assess_agent` only stores the manager's decision and reason; it does not
contact another agent or model. The manager must consider descriptions and limits,
not just skill names. For example, Robot advertises a maximum payload of 2 kg and
flat indoor floors, so the manager should reject a 20 kg pickup request.

The runtime enforces these conditions:

- A positive assessment requires a discovered card covering every required skill.
- `found` requires a positive assessment of that card.
- Explicit user skill IDs cannot change. Changing inferred requirements clears
  previous assessments; refreshing a card clears that card's assessment.
- `not_found` requires inspecting the entire roster and a negative assessment for
  every card covering the required skills. If any cards remain unavailable, the
  result is `error` with an incomplete-search explanation.
- The default budget is 16 model turns and 600 seconds; exceeding either produces
  `error` and preserves completed history.

Free-text limits are interpreted by the manager LLM, not independently verified
by code or hardware. The search selects one agent for the entire request; it does
not divide work across a team.

## Code layout

```text
agents/
├── manager_agent/
│   ├── manager_agent.py           # Autonomous tool loop and manager instructions
│   ├── manager_tools.py           # Card discovery, assessments and result checks
│   ├── manager_agent_executor.py  # User A2A request adapter
│   ├── manager_agent_card.json
│   └── __main__.py
├── language_agent/                # Class, JSON card and server entry point
├── analytics_agent/               # Class, JSON card and server entry point
├── robot_agent/                   # Class, JSON card and server entry point
├── common/
│   ├── task_executor.py           # Shared explicit worker task handler
│   ├── search_models.py           # Search request/result schemas
│   ├── agent_card_loader.py
│   ├── a2a_client.py
│   ├── ollama_client.py
│   ├── server_runtime.py
│   └── settings.py
├── client.py
├── check_ollama_running.py
└── run_all.py
```

Edit each adjacent `*_agent_card.json` to change its skills or limits, then restart
that agent. Configured URLs override the JSON URL. The A2A `skills` field contains
task abilities; `capabilities` describes transport features such as streaming.
Worker JSON-RPC endpoints accept separate `agent-task/v1` execution requests. No negotiation is performed.

## Run

Use Python 3.12+ and your existing virtual environment:

```bash
cd ~/multi-agentic-auth/a2a-multi-agent
source ~/venv/bin/activate
python -m pip install -e .
# Initial setup only; preserve any existing .env edits.
cp .env.example .env
```

The default Ollama endpoint is `http://127.0.0.1:11435/v1`. Both manager and worker models are required for selection and explicit tasks:

```bash
export OLLAMA_HOST=127.0.0.1:11435
ollama list
ollama pull gpt-oss:20b
ollama pull llama3.2:3b
python -m agents.check_ollama_running
python -m agents.run_all
```

Keep Ollama running. Wait for all four servers to start. Ctrl+C stops the team;
if a child exits, the launcher stops the others. Alternatively start each agent
in a separate terminal with `python -m agents.manager_agent`,
`python -m agents.language_agent`, `python -m agents.analytics_agent`, and
`python -m agents.robot_agent`.

In another terminal with the same environment:

```bash
python -m agents.client "Find an agent to pick up a 1 kg box and move it indoors." --json
python -m agents.client "Translate written English text into Spanish." --capability translation --json
python -m agents.client "Find anomalies in supplied numerical readings." --capability anomaly_detection --json
python -m agents.client "Pick up a 20 kg box." --capability picking --json
python -m agents.client "Find an agent that can fly." --capability flying --json
python -m agents.client "Translate text and pick up boxes." --capability translation --capability picking --json
```

Expected selections: Robot, Language, Analytics; the last three requests should
produce `not_found`. Natural-language requirements and free-text feasibility
judgments depend on the model. Explicit `--capability` values fix skill IDs but
do not replace the manager's assessment of constraints.

## Results and diagnostics

Results contain `status` (`found`, `not_found`, `input_required`, or `error`),
`selected_agent` (name, URL, advertised capabilities and manager's `reason`),
`assessments`, `discovered_agents`, `attempts`, `manager_actions`, and
`manager_turns`. There is no `offer_id`. `attempts` records discovery errors and
manager assessments, not worker replies. If the manager asks a question, send a
new request containing the original task and the answer; searches are independent.

`--trace` displays executed tool actions. `--thoughts` displays reasoning text
returned by Ollama after the search completes. Provider `reasoning`,
`reasoning_content`, and `thinking` string fields are preserved verbatim without
an extra inference call. Missing reasoning is reported explicitly; ordinary
answer text is not substituted. `--json` stays valid JSON with either flag.
Streaming is disabled. Historical `out.json`, if present, is an old run artifact;
run the client again to generate output in the current format.

Client exit codes are 0 for `found`, 1 for other structured results, and 2 for
handled HTTP/configuration failures. Discovery failure is not evidence that an
agent lacks a skill. A successful selection can still finish early even when a
different agent was unreachable.

Configure `MANAGER_MODEL`, `TOOL_LLM_URL`, `MANAGER_MAX_TURNS`,
`MANAGER_TIMEOUT_SECONDS`, `LLM_TIMEOUT_SECONDS`, `A2A_TIMEOUT_SECONDS`, and the
four `*_URL` values in `.env`. Optional comma-separated `AGENT_URLS` overrides the
worker roster. Keep the A2A timeout above the manager deadline. Exported variables
override `.env`; restart affected processes after changes. `SUBAGENT_MODEL` sets the shared worker model (default `llama3.2:3b`).
`ROBOT_AVAILABLE` is no longer used. Availability is not checked during selection.

```bash
curl http://127.0.0.1:10003/.well-known/agent-card.json
curl http://127.0.0.1:11435/v1/models
python -m unittest discover -s tests -v
```

Tests use real A2A SDK discovery and manager JSON-RPC messaging through ASGI
transports, with deterministic model doubles. They cover card-only worker traffic,
selection guards, constraint rejection, retries, independent concurrent searches,
changed requirements/cards, deadlines and model failures. They do not validate a
live model's judgment. Local clients ignore proxy environment variables. The demo
has no authentication or IoTAuth integration and is intended for local development.


## Explicit worker tasks

Selection via `agents.client` remains card-only and never automatically dispatches
work. To ask a worker to perform a task, use the separate client:

```bash
python -m agents.task_client "Translate to Korean: Hello, how are you?" --agent language --json
python -m agents.task_client "Calculate the mean of these values: 2, 4, 6." --agent analytics --json
python -m agents.task_client "Plan moving a 1 kg box to station B on a flat indoor floor." --agent robot --json
```

You can also pass `--url` with the selected agent's URL instead of `--agent`.
The A2A `DataPart` request is `{"protocol":"agent-task/v1", "action":"execute",
"query":"..."}`. Plain-text A2A messages are also accepted. This is a one-shot
request/response interface; it does not preserve conversation state or negotiate.

Workers share `TaskAgent` and `TaskExecutor`, with their own model clients, cards,
and role instructions. Language generates translations or summaries; Analytics
produces LLM-based analysis of supplied data (no code execution or live sensors).
Robot produces plans only: the runtime reports successful robot responses as
`simulated`, never `completed`, and sends no hardware commands. Actual robot
execution requires a future hardware/tool adapter.

Results contain `agent_name`, `status`, and `output`. Status is `completed`,
`simulated`, `input_required`, `declined`, or `error`. The task client exits with
0 for completed/simulated results, 1 for other structured results, and 2 for
handled HTTP/configuration failures. Missing inputs and unsupported tasks are
handled by the worker LLM using its declared card limits. Model errors are returned
as structured errors. The default worker model can be changed with
`SUBAGENT_MODEL` in `.env`; restart workers after changing it. Constructing an
agent without a model is supported for card-only tests, but task requests then
return an explicit configuration error.
