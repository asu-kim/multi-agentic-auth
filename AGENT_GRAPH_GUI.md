# Python agent graph builder

From the repository root, run:

```sh
python3 agent_graph_gui.py
```

The GUI opens in a normal window. **Save**, **Save the file and generate SST**, and **Run Auth server** appear together in one bottom row. Press **Escape** to leave full screen or **F11** to toggle it (some Mac keyboards require **Fn+F11**).

Requires Python 3.9+ with Tkinter and a desktop session. There are no pip dependencies.
If your interpreter reports missing `_tkinter`, follow the [Tkinter troubleshooting steps](#troubleshooting-tkinter-is-required) below.

1. Set the network, Auth host, Auth ID, and output filename. Defaults are `net1`, `localhost`, `101`, and `my_agents.graph`.
2. Add agents (including human users) and resources. For example, enter `Manager Agent`, `forklift agent`, and `Resource`. Names become `net1.managerAgent`, `net1.forkliftAgent`, and `net1.resource`. Leave Group blank to derive `ManagerAgents`, `ForkliftAgents`, or `Resources`, or enter your own group. Resources need a host and port; agent host/port fields are ignored.
3. Add rules using the group selectors:
   - `DelegationGrant`: Actor `ManagerAgents`, Recipient `ForkliftAgents`, Resource `Resources` allows the manager group to grant access to the forklift group. It also generates a matching `DelegationRevoke` for that same actor, recipient, and resource.
   - `DelegationRevoke`: select the group allowed to revoke, the recipient, and the resource. Validity is ignored.
   - `DirectAccess`: select the actor and resource to create an ordinary communication policy. Recipient is ignored.
4. Review the generated JSON in Graph preview, then click Save graph. Files are always written to `iotauth/examples/configs`, regardless of the current working directory. Existing files require overwrite confirmation.

Rules apply to every entity in the selected groups. A delegation privilege is permission to issue a grant; it does not itself give the recipient direct access. Each delegation privilege (grant or revoke) automatically adds a `commPolicies` entry giving the actor group access to the resource group: `MaxNumSessionKeyOwners: 2`, `SessionCryptoSpec: AES-128-CBC:SHA256`, `AbsoluteValidity: 1*day`, `RelativeValidity: 6*hour`, `Expiration: Infinity`, `IsDelegated: 0`, and `Context: null`. These policy defaults are independent of the grant validity. Resource endpoints are copied to the actor and, for grants, the recipient. Matching automatic and explicit revoke entries are deduplicated; multiple recipients sharing an actor/resource pair produce one communication policy. An explicit `DirectAccess` rule for that pair overrides the automatic policy validity, regardless of input order. Generated entries appear in Graph preview and the saved graph; removing a grant removes its derived entries unless another remaining rule requires them. To change an entity or rule, remove it and add the corrected entry; remove dependent rules first when deleting the last member of a group.

The first version supports a single Auth and one network, TCP entities, permanent distribution keys, and the crypto defaults used in `remote_server.graph`. Auth ports are 21900 (TCP), 21902 (UDP), 21901 (Auth), and 21903 (callback). Validity syntax is a positive number followed by `*sec`, `*min`, `*hour`, or `*day`. Saving generates the graph only. The **Save the file and generate SST** button also runs the existing credential/configuration/database generation pipeline. It does not start agents or issue runtime grants.

Enter the credential password in the masked GUI field, then click **Save the file and generate SST**. This saves the graph, runs `./cleanAll.sh`, and only after cleanup exits successfully runs `./generateAll.sh -g configs/<filename>.graph -p <password>`. Both scripts run from `iotauth/examples`. Cleanup removes existing example credentials, generated configurations, and Auth databases; graph files are retained. If cleanup fails or overwrite confirmation is cancelled, generation does not run.

The button is disabled while running; the GUI displays the current stage and reports success or failure. Output remains in the launch terminal, but no terminal password entry is needed. Keep the GUI open until completion: it starts generation after observing cleanup finish. Closing the GUI does not stop an already running script.

The password field is cleared after starting and is never saved to the graph or guide. The existing script's `-p` interface exposes passwords in process arguments, so use this for local examples only. Passwords must have at least four characters and cannot contain whitespace or `*`, `?`, `[`, `]`, or null characters because the existing script expands the password without quotes. Invalid passwords are rejected before cleanup. The script is not modified.

The generation script installs required Node dependencies and generates credentials, configurations, and Auth databases inside `iotauth`. Its usual tools (including Node/npm, OpenSSL, Maven, and Java) are required.

You can also run the same pipeline manually:

```sh
cd iotauth/examples
./cleanAll.sh && ./generateAll.sh -g configs/my_agents.graph
```

`agent_graph_gui.py` also exposes `build_graph(entities, rules, network, auth_host, auth_id)` and `save_graph(graph, filename)` for use by other Python tools. Importing the module does not open a GUI or require Tkinter.

Run focused tests from the repository root:

```sh
python3 -B -m unittest test_agent_graph_gui -v
```

## Troubleshooting: Tkinter is required

If launching the GUI prints:

```text
Tkinter is required. Run with a Python installation that includes Tk support.
```

The selected Python cannot import Tkinter. A virtual environment uses its base Python's Tk support; activating `venv` does not install it. `pip install tkinter` is not the fix.

### 1. Check the interpreter inside your active virtual environment

Run these commands in the same terminal where the error occurred:

```sh
python3 -c 'import sys; print("Python:", sys.version); print("Executable:", sys.executable); print("Base installation:", sys.base_prefix)'
python3 -c 'import tkinter; print("Tk version:", tkinter.TkVersion)'
```

The second command exposes the underlying import error, such as `No module named '_tkinter'`. Use the reported Python major/minor version when installing Tk support. The virtual environment may use a different version from your default Python outside it.

### 2. Install matching Tk support for Homebrew Python

If the base installation is a Homebrew Python 3.14 installation, run:

```sh
/opt/homebrew/bin/python3.14 -m venv venv
source venv/bin/activate
brew install python-tk@3.14
python3 -m tkinter
```

For another Homebrew Python version, use the matching formula, for example `python-tk@3.13` for Python 3.13. Installing Homebrew Tk support does not repair a Python installed by a different provider.

`python3 -m tkinter` should open a small test window. Close it, then run:

```sh
python3 agent_graph_gui.py
```

See the official [Homebrew Tk formula](https://formulae.brew.sh/formula/python-tk%403.14) and [Python Tkinter documentation](https://docs.python.org/3/library/tkinter.html).

### 3. If the existing virtual environment still fails

First test the matching Homebrew interpreter directly. This example uses Python 3.14:

```sh
"$(brew --prefix python@3.14)/bin/python3.14" -m tkinter
```

If that opens successfully, you can run the GUI directly with it; the GUI has no pip dependencies:

```sh
"$(brew --prefix python@3.14)/bin/python3.14" agent_graph_gui.py
```

Alternatively, create a separate GUI environment with that interpreter. Use a new directory name if `.venv-gui` already exists:

```sh
"$(brew --prefix python@3.14)/bin/python3.14" -m venv .venv-gui
.venv-gui/bin/python -m tkinter
.venv-gui/bin/python agent_graph_gui.py
```

This leaves your existing `venv` in place. If the direct interpreter test also fails, resolve that installation's Tk error before creating a new environment.

### Tk imports, but the window hangs or does not appear

An import alone does not verify that Tk can display a window. On the development Mac, `/usr/bin/python3` imported Tk 8.5, but the GUI startup test stalled, so it is not a verified fallback. Test a current Python with matching Tk support using `python3 -m tkinter` first. Run the GUI from a local desktop terminal; a headless or remote shell may lack a graphical display.

### Script filename error after copying a command

If Python says it cannot open the script, retype `python3 agent_graph_gui.py` without trailing characters. Copied commands can contain an invisible non-breaking space after `.py`. Run from the `multi-agentic-auth` root. Generated graphs are saved to `iotauth/examples/configs`.


## Run the Auth server

Launch the GUI from a local terminal and click the separate **Run Auth server** button. It runs these commands, in order, from `iotauth/auth/auth-server`:

```sh
make
java -jar target/auth-server-jar-with-dependencies.jar -p ../properties/exampleAuth101.properties
```

The Java command runs only if `make` succeeds and the built JAR exists. Generate the Auth101 configuration first; this button uses `exampleAuth101.properties` regardless of the graph editor's Auth ID. The build needs make, Maven, and a compatible JDK.

When Java displays `Please enter Auth password:` in the terminal where you launched the GUI, enter the password used to generate the credentials. The server reads it with hidden input. The GUI does not store this password or pass it in command arguments. The credential password field belongs to generation; server startup requests the password separately when Java needs it.

Build/server output and interactive commands stay in the launch terminal. The GUI remains responsive and reports build or server failures. While building or running, duplicate launches and cleanup/generation are disabled. Enter `quit` in the terminal to stop Auth; the buttons become available when the process exits. An active-process status is not a readiness check—check the server output for successful initialization or a password error. Keep the GUI open during the build so it can start Java afterward; closing it does not stop an already running server.


## Let the manager select resources

When adding a Resource entity, use the optional **Resource description** field to explain its purpose, location, and what access enables (for example, “Warehouse entrance door used to reach the middle-shelf storage area”). This text is saved as entity metadata in the graph. Use descriptions of resources, not instructions for the manager.

In `a2a-multi-agent/.env`, set `IOTAUTH_GRAPH_FILE=../iotauth/examples/configs/my_agents.graph` (replace the filename with the one you saved) and `IOTAUTH_MANAGER_ENTITY=net1.managerAgent`. The manager reads resource groups, entity names/descriptions, parent access policies, and delegation grant privileges from this file. Save changes before sending a new task; each task uses its own catalog snapshot. Provision Auth from the same graph so the catalog matches the deployed policies.

Users may omit `--resource`: the manager selects a matching resource through its tools or asks if ambiguous. If `--validity` is omitted too, the matching graph grant validity supplies the duration. Explicit choices are preserved. Selection remains at resource-group granularity, and this version supports one resource group per task.


## Agent URLs

The entity form shows **Kind**, **Name**, **Group**, and **Agent URL** in one row. Kind, Name, and Group are compact; the URL field uses the remaining space. Agent URL appears only for Agent entities. Use an HTTP(S) endpoint such as `http://127.0.0.1:10003/`; leave it blank for entities without an A2A service, such as human users.

URLs are displayed in the entity list but are **not stored in the graph**. Saving updates `a2a-multi-agent/.env`: Manager Agent maps to `MANAGER_URL`, Drone Agent to `DRONE_URL`, Rover Agent to `ROVER_URL`, and Forklift Agent to `FORKLIFT_URL`. Other names use an uppercase underscore-separated variable name with `_URL`. `IOTAUTH_AGENT_GROUPS` maps the entered URLs to their GUI groups. Old endpoint mappings for updated agents are replaced; unrelated settings and agents are preserved. If `.env` is missing, it is initialized from `.env.example`. Blank agent URLs leave existing settings unchanged.

Restart the agents/manager after saving to reload `.env`. If you set an explicit `AGENT_URLS` roster, update it separately to match; the GUI does not overwrite that roster or start agents.
