# A2A card-based agent selection

An autonomous LLM manager reads independent agents' **A2A Agent Cards**, compares
skills and declared limits with the user's request, and selects **one** suitable
agent. There is no proposal/confirmation negotiation, task reservation, before authority delegation. After successful delegation, the manager dispatches a worker task. A `found` result means the card appears suitable;
it does not confirm current availability or actual ability to execute the task.

| Agent | Model | Port | Advertised skill IDs |
| --- | --- | --- | --- |
| Manager | `gpt-oss:20b` | 10000 | `agent_discovery` |
| Language | `llama3.2:3b` | 10001 | `translation`, `summarization` |
| Analytics | `llama3.2:3b` | 10002 | `statistics` |
| Robot | `llama3.2:3b` | 10003 | `picking`, `mobility`, `secure_communication` |

Each agent is a separate server process with its own stored JSON card. The manager
knows configured addresses only; it learns abilities by fetching cards over HTTP
using the A2A SDK's card resolver. User requests to the manager still use A2A 0.3
JSON-RPC `message/send` through `ClientFactory`. After successful IoTAuth delegation, the manager sends a task message to the selected worker. All four agents have their own Ollama client. Worker inference runs only for explicit task requests; card discovery never calls a worker model.

## Decision loop

```text
User -> A2A -> Manager LLM
                 |
                 +-> discover_agent: fetch an A2A card
                 +-> set_requirements: interpret all required skills
                 +-> assess_agent: record the manager's own card-based judgment
                 +-> delegate_authority: grant the selected agent's group authority through IoTAuth
                 +-> dispatch_task: send an A2A task after successful delegation
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
python -m agents.client "Find an agent to pick up a box and move it indoors." --json
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
live model's judgment. Local clients ignore proxy environment variables. The A2A endpoints have no authentication and are intended for local development.
Agent authority delegation uses the configured IoTAuth manager identity.


## Explicit worker tasks

The manager dispatches work after successful delegation. Robot selections receive
a secure-connection task; other workers receive the original request. To ask a worker to perform a task, use the separate client:

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
Robot produces simulated plans for physical tasks and sends no hardware commands.
Its secure-communication tool can return `completed` after a confirmed IoTAuth connection. Actual robot
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

## Manager authority delegation

After identifying and positively assessing an agent with the desired capabilities,
the manager calls `delegate_authority` with that agent's `agent_url`, before
`finish_search`. The tool resolves the selected URL through `IOTAUTH_AGENT_GROUPS`
and sends `delegateAuthority <configured-group> Resources 1*day`. It does not
infer authorization identities from display names or untrusted card text.

Set `IOTAUTH_AGENT_GROUPS` to a JSON object mapping each selectable A2A URL to its
actual registered IoTAuth group. The groups defined in `iotauth/examples/configs/remote_server.graph` are:

```dotenv
IOTAUTH_AGENT_GROUPS='{"http://127.0.0.1:10000/":"ManagerAgents","http://127.0.0.1:10001/":"LanguageAgents","http://127.0.0.1:10002/":"AnalyticsAgents","http://127.0.0.1:10003/":"RobotAgents"}'
```

The mapping includes Manager, Language, Analytics, and Robot. Manager remains
outside the default selectable worker roster. Keep these URLs aligned with the
deployment and ensure Auth grants the manager delegation privileges for each subject. There is no hard-coded fallback subject;
a missing mapping reports an error without sending a command. URL matching ignores
a trailing slash. This grants authority to the selected agent's configured **group**,
which may contain more than one entity.

Provision the manager's IoTAuth config, keys, and Auth registration first. This
checkout does not include `configs/net1/managerAgent.config`; the integration
reports a configuration error until it is supplied. Set `IOTAUTH_MANAGER_CONFIG`
if its path differs (the directory is `configs`, not `config`). Install the Node
entity dependencies and run the corresponding IoTAuth Auth service as usual.

The tool launches `node user.js configs/net1/managerAgent.config --privilege-json`
from the sibling `iotauth/entity/node/example_entities` directory. It waits for
initialization to finish before writing the command to stdin. The optional flag
adds JSON events while preserving normal interactive logs. Success requires both
`received privilege response!` and `Finished privilege request`, plus a structured
`DelegationGrant` policy with `IsDelegated: 1`. The returned policy is preserved in
`manager_actions`; it may contain a concrete target such as `ResourceA` rather
than the requested collection `Resources`.

`IOTAUTH_ENTITY_DIR`, `IOTAUTH_MANAGER_CONFIG`, `IOTAUTH_NODE`, and
`IOTAUTH_TIMEOUT_SECONDS` configure the bridge. A timeout or failed response is
reported as an error; a sent request may already have changed Auth state, so it
is not retried automatically. Repeated tool calls within the same search reuse
the first result. The subprocess is cleaned up on success, failure, or cancellation.
This does not send a task to the Robot or verify its physical availability.

## Robot secure communication tool

The robot can select `establish_secure_communication` through its structured LLM
action response. Send an explicit worker task:

```bash
python -m agents.task_client "Establish secure communication with the configured resource." --agent robot --json
```

The tool launches `node user.js configs/net1/robotAgent.config`,
waits for the complete `current parameters: {...}` initialization log, then sends
`initComm net1.resource`. Success requires the exact log `switching to IN_COMM`. The robot server
keeps the Node process alive, reuses a live connection, and closes it on shutdown.
Failures and timeouts return task status `error`; confirmed connection returns
`completed`. Physical tasks still return simulated plans. After successful delegation the manager dispatches a secure-connection task to the selected robot.

Configure `IOTAUTH_ROBOT_CONFIG` (use `config/net1/robotAgent.config` if that is your
actual path), `IOTAUTH_ROBOT_RESOURCE`, and the shared `IOTAUTH_ENTITY_DIR`,
`IOTAUTH_NODE`, and `IOTAUTH_TIMEOUT_SECONDS`. Provision the robot credentials and
Auth policy and start the resource server first. Its name must appear in the robot
config's `targetServerInfoList`. This tool connects only; it sends no application
payload or physical robot commands.

## Manager-to-worker handoff

The manager now follows assessment → authority delegation → `dispatch_task` →
`finish_search`. `dispatch_task` uses the selected discovered card and A2A
`agent-task/v1`, and requires a current positive assessment plus confirmed delegation.
For a robot advertising `secure_communication`, it asks the robot to establish a
connection to its configured `IOTAUTH_ROBOT_RESOURCE`; it does not start physical
pickup or movement. Other selected workers receive the original request.

The worker result appears in `task_result`, the action trace, and the final summary.
Errors/declines become overall `error`, and requests for clarification become
`input_required`. Duplicate dispatch calls within a search reuse the recorded result;
uncertain failures are not automatically retried. The robot service must be running
with its Node config and resource target configured. Merely finding an agent does
not prove that its task succeeded; inspect the worker result.

### Explicit robot tool dispatch

Manager connection requests include `requested_tool: "establish_secure_communication"`
in the `agent-task/v1` payload. Robot executes this allowlisted, zero-argument tool
directly, without asking its LLM to reinterpret the request. The tool reads
`IOTAUTH_ROBOT_CONFIG` and `IOTAUTH_ROBOT_RESOURCE`; credentials stay in the Node
configuration. Missing files, denied authorization, and connection failures are
reported by the tool, not guessed by the model. Ordinary natural-language robot
requests still use LLM action selection. Other workers reject explicit tools they
do not implement. Restart both manager and robot after updating the code.
