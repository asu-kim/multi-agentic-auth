# Multi-Agentic Auth

Multi-Agentic Auth combines A2A agent discovery and task execution with IoTAuth access delegation. An agent's advertised skills describe what it can do; authorization policies determine which protected resources it may access. The manager selects a worker using its Agent Card, delegates access through Auth, dispatches the task, and requests revocation after the worker reports completion.

Use the Python GUI to register entities, configure resource endpoints, and define group-based delegation privileges. It generates the system graph used to provision IoTAuth and saves agent service URLs separately in the A2A application's environment file.

| Component | Purpose |
| --- | --- |
| `agent_graph_gui.py` | System configuration GUI |
| `iotauth/` | Auth server, secure communication entities, and provisioning scripts |
| `a2a-multi-agent/` | Manager, example workers, Agent Cards, and task clients |
| [GUI guide](AGENT_GRAPH_GUI.md) | Detailed GUI behavior and Tkinter troubleshooting |
| [Agent runtime guide](a2a-multi-agent/README.md) | Agent installation, models, tools, and task execution |

## 1. Prepare the environment

Run the commands below from the repository root. For a fresh checkout, initialize the IoTAuth submodule:

```sh
git clone https://github.com/asu-kim/multi-agentic-auth.git
git submodule update --init --recursive
```

The GUI requires **Python 3.9 or newer with Tkinter** and a desktop session. It has no pip dependencies. Generating credentials and configurations additionally requires the IoTAuth toolchain: Node.js/npm, OpenSSL, Java/JDK, and Maven. Building Auth also requires `make`. The A2A application has separate Python 3.12+ and model requirements described in its [runtime guide](a2a-multi-agent/README.md#run).

Verify that your selected Python can open a Tk window, then launch the GUI.
If the base installation is a Homebrew Python 3.14 installation, run:

```sh
/opt/homebrew/bin/python3.14 -m venv venv
source venv/bin/activate
brew install python-tk@3.14

python3 -m tkinter
# Close the test window before continuing.
python3 agent_graph_gui.py
```

Launch from a terminal: generation logs and the Auth server's password prompt appear there. The GUI opens in a normal window, with **Save**, **Save the file and generate SST**, and **Run Auth server** together at the bottom.

## 2. Set the deployment configuration

Fill in the settings at the top of the GUI:

| Field | Example | Meaning |
| --- | --- | --- |
| Network | `net1` | Prefix for generated entity names |
| Auth host | `localhost` | Auth address reachable by the entities; use a reachable host for remote deployments |
| Auth ID | `101` | Identifier of the Auth server |
| Filename | `my_agents.graph` | Graph filename under `iotauth/examples/configs/` |
| Credential password | Your chosen password | Password used during credential and database generation |

Keep **Auth ID 101** when using the GUI's **Run Auth server** button: that button currently starts `exampleAuth101.properties` regardless of the Auth ID entered above.

## 3. Register agents and resources

Open **1. Agents and resources**. Fill in an entity and click **Add entity** for each one.

### Agents

Choose **Kind: Agent**, then enter **Name**, **Group**, and **Agent URL**. Host and Port are resource-only fields and are hidden for agents. For a local example:

| Name | Group | Agent URL |
| --- | --- | --- |
| Manager Agent | `ManagerAgents` | `http://127.0.0.1:10000/` |
| Drone Agent | `DroneAgents` | `http://127.0.0.1:10001/` |
| Rover Agent | `RoverAgents` | `http://127.0.0.1:10002/` |
| Forklift Agent | `ForkliftAgents` | `http://127.0.0.1:10003/` |

Replace loopback addresses with reachable service addresses when agents run on other machines. The URL identifies an existing A2A service; entering it does not create or start that service. Human users can also be registered as Agent entities, with the URL left blank.

Names are normalized: `Manager Agent` becomes `net1.managerAgent`. A blank Group is derived from the name, such as `ManagerAgents`. Entities in the same group share group-based permissions.

### Resources

Choose **Kind: Resource**. Enter its Name and Group, then its **Host**, **Port**, and optional **Description**. For example:

| Name | Group | Host | Port | Description |
| --- | --- | --- | --- | --- |
| Warehouse Door | `Doors` | `localhost` | `21100` | Entrance door to the warehouse storage area |

Host and Port identify the protected resource's communication endpoint, not an agent's A2A URL. Use a host reachable by the connecting worker. Descriptions are saved in the graph as resource metadata and help the manager select a resource when a task omits one. Describe the resource's purpose and location.

To correct an entity, remove it and add the corrected entry. Remove dependent rules first when deleting the last entity in a referenced group. The GUI currently starts with an empty form; it does not import an existing graph for editing.

## 4. Configure delegation privileges

Open **2. Privileges**. Select the groups and click **Add rule**. To allow the manager to delegate door access to the forklift, enter:

| Field | Value |
| --- | --- |
| Type | `DelegationGrant` |
| Delegator | `ManagerAgents` |
| Delegatee | `ForkliftAgents` |
| Resource | `Doors` |
| Validity | `1*day` |

This defines the manager's **permission to grant access**. It does not immediately grant the forklift access. Runtime delegation happens when the manager calls `delegate_authority`.

Adding this rule automatically generates:

- A matching `DelegationRevoke` privilege for the same delegator, delegatee, and resource.
- A `commPolicies` entry granting the delegator group access to the resource group, with `MaxNumSessionKeyOwners: 2`, `SessionCryptoSpec: AES-128-CBC:SHA256`, absolute validity `1*day`, relative validity `6*hour`, and `IsDelegated: 0`.

The automatic parent communication policy's validity is independent of the delegation rule's validity. Resource endpoint information is also included for the delegator and grant recipient.

Use **DirectAccess** to configure ordinary access from the Delegator group to the Resource group; Delegatee is ignored. An explicit DirectAccess rule overrides the automatic policy validity for that pair. **DelegationRevoke** can also be added explicitly; its Validity field is ignored. Matching automatic and explicit entries are deduplicated.

Validity values use a positive number followed by `*sec`, `*min`, `*hour`, or `*day`, such as `30*min`. Rules apply to whole groups, so choose group membership deliberately.

## 5. Review and save

Open **3. Graph preview** and click **Refresh preview** to review the generated JSON, including automatic revoke privileges and communication policies. Click **Save** to write the configuration without running provisioning.

| Output | Contents |
| --- | --- |
| `iotauth/examples/configs/<filename>.graph` | Auth configuration, entities, resource endpoints/descriptions, communication policies, and delegation privileges |
| `a2a-multi-agent/.env` | Entered agent URLs and the `IOTAUTH_AGENT_GROUPS` URL-to-group mapping |

**Agent URLs are not stored in the graph.** The GUI derives environment variable names from agent names: `Manager Agent` updates `MANAGER_URL`, `Drone Agent` updates `DRONE_URL`, `Rover Agent` updates `ROVER_URL`, and `Forklift Agent` updates `FORKLIFT_URL`. A different name creates a corresponding variable; for example, `A` creates `A_URL`, not `DRONE_URL`.

The GUI preserves unrelated environment settings and initializes a missing `.env` from `.env.example`. Blank agent URLs leave existing endpoint settings unchanged. Existing graphs require overwrite confirmation. Removing an entity from the form does not automatically delete its old environment settings; review `.env` when removing or renaming agents.

## 6. Generate SST configuration and credentials

Enter the **Credential password**, then click **`Save the file and generate SST`**. The GUI saves the graph and environment settings, then runs these stages from `iotauth/examples`:

1. `./cleanAll.sh`
2. `./generateAll.sh -g configs/<filename>.graph -p <password>`

Stop the Auth server before regenerating. Generation runs only if cleanup succeeds.

The password must contain at least four characters and must not contain whitespace, `*`, `?`, `[`, `]`, or null characters. It is cleared from the GUI after startup and is not saved in the graph or `.env`. The existing generation script receives it as a command-line argument, so it may be visible in process listings.

Keep the GUI open until generation finishes; it must observe cleanup completion to start generation. Check the launch terminal for detailed output and the GUI status for success or failure. Provisioning creates credentials, entity configurations, and Auth databases within `iotauth`; it does not start agents or protected resource servers.

## 7. Start Auth

After successful generation, click **`Run Auth server`**. From `iotauth/auth/auth-server`, the GUI runs:

```sh
make
java -jar target/auth-server-jar-with-dependencies.jar -p ../properties/exampleAuth101.properties
```

Java starts only after a successful build. When the launch terminal displays `Please enter Auth password:`, enter the password used during generation. This is a separate terminal prompt; the GUI's credential field is used for generation only.

Check the terminal for successful Auth initialization. _**Enter `quit` there to stop Auth after all experiment**_. Duplicate Auth launches and GUI cleanup/generation are disabled while this server is active. Closing the GUI does not stop an already running Auth process.

## 8. Connect the agent runtime

In `a2a-multi-agent/.env`, point the manager at the graph you provisioned and its generated entity name:

```dotenv
IOTAUTH_GRAPH_FILE=../iotauth/examples/configs/my_agents.graph
IOTAUTH_MANAGER_ENTITY=net1.managerAgent
```

Review the IoTAuth manager/worker configuration paths and resource adapter settings using the [agent runtime guide](a2a-multi-agent/README.md). If you configured an explicit `AGENT_URLS` roster, update it separately; the GUI does not overwrite it. Restart agents after changing `.env`, and do not replace the GUI-updated `.env` with `.env.example`.

Start the protected resource service and the A2A services as described in that guide. The GUI does not create Agent Cards, implement worker skills, launch the agent team, or connect hardware adapters.

At runtime, the manager uses Agent Cards to select a suitable worker. When the user omits a resource, it can select an authorized resource group from the graph or ask for clarification; an omitted delegation duration comes from the matching grant privilege. After confirmed delegation, it dispatches the task. On a `completed` or `simulated` worker result, it calls `revoke_authority`, which sends `revoke <delegatee> <delegated_object>` through `user.js` using the confirmed grant's scope. Unconfirmed revocation is reported as an error. This is active-request cleanup, not recovery after a manager crash, and policy revocation does not itself close existing secure sessions.

The current GUI supports one network and one Auth, TCP entities, permanent distribution keys, and fixed cryptographic defaults. The example manager selects one worker and one resource group per task. Custom agents need compatible A2A cards and task handling, plus appropriate resource adapters; registering a URL alone does not supply these capabilities.

## Troubleshooting

- **“Tkinter is required” or missing `_tkinter`:** use Python with matching Tk support. A virtual environment inherits its base interpreter's Tk installation; `pip install tkinter` does not fix it. Test with `python3 -m tkinter` and follow the [detailed troubleshooting guide](AGENT_GRAPH_GUI.md#troubleshooting-tkinter-is-required).
- **Agent URL did not update the expected `.env` variable:** check the entity name and click Save. Variable names are derived from names, not groups or row positions.
- **Generation failed:** inspect the terminal for the failed cleanup/generation stage, missing dependencies, or password errors.
- **Auth failed to start:** verify that generation succeeded, Auth101 properties exist, and the entered password matches the generation password.
- **Manager cannot find a resource or delegate access:** verify the graph path, manager entity name, URL-to-group mapping, and grant rule. The runtime catalog and deployed Auth policies must come from the same configuration.

For further details, see the [GUI guide](AGENT_GRAPH_GUI.md) and [agent runtime guide](a2a-multi-agent/README.md).
