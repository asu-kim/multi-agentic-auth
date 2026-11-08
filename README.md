# Multi-Agentic Auth

Multi-Agentic Auth combines Agent-to-Agent (A2A) task coordination with IoTAuth/Secure Swarm Toolkit (SST) access delegation for multi-agent systems. A manager discovers worker capabilities, selects a suitable worker, and grants it temporary access to the protected resources needed for a task. After the worker reports completion, the manager requests revocation of the delegated permissions.

Agent Cards describe what a worker can do; Auth policies determine which resources it may access. The repository brings together the agent runtime, authorization infrastructure, a configuration GUI, and a warehouse simulation to demonstrate this workflow.

For detailed installation, configuration, and execution instructions, see the [deployment README](a2a-multi-agent/README.md) and the [simulator README](a2a-multi-agent/simulation/README.md).

## How it works

1. **Configure the system.** Register agents and resources, define group-based access and delegation privileges, and generate IoTAuth credentials and configurations with the graph GUI.
2. **Discover and select a worker.** The manager reads A2A Agent Cards and assesses their skills and limits against the user's task.
3. **Delegate access.** The manager requests authorization from Auth for the required resource groups and duration. Dispatch requires confirmed delegation.
4. **Execute the task.** The manager sends the task to the selected worker over A2A. The worker uses SST to communicate with protected resources.
5. **Revoke access.** When the worker reports `completed` or `simulated`, the manager requests revocation of the confirmed grants and returns the task and revocation results.

The current example assigns one worker per task. Revocation removes delegated access policies; it does not terminate existing secure sessions or invalidate previously issued keys. Cleanup runs within the active manager request, so interrupted tasks require inspection of Auth state.

## Repository layout

| Path | Purpose |
| --- | --- |
| [`a2a-multi-agent/`](a2a-multi-agent/) | Python A2A runtime, Manager, Drone, Rover, and Forklift agents, Agent Cards, task clients, and latency recording. |
| [`a2a-multi-agent/simulation/`](a2a-multi-agent/simulation/) | Gazebo warehouse world, simulation bridge, and simulator deployment instructions. |
| [`iotauth/`](iotauth/) | IoTAuth submodule containing Auth, SST communication clients, protected resource examples, and provisioning tools. |
| [`agent_graph_gui.py`](agent_graph_gui.py) | GUI for registering entities, defining policies, generating deployment configurations and credentials, and launching Auth. |
| [`AGENT_GRAPH_GUI.md`](AGENT_GRAPH_GUI.md) | Detailed GUI usage and Tkinter troubleshooting. |

## Warehouse demonstration

The example team includes a Manager and Drone, Rover, and Forklift workers with different advertised shelf and payload capabilities. A delivery task requires access to both a target item and a gate. The selected worker requests item retrieval and gate opening over SST, then completes delivery to the secure zone.

The Gazebo scene visualizes delivery using a forklift model. Worker capabilities are modeled, and Gazebo execution returns a `simulated` result. The documented setup runs Auth, the LLM service, and agents on an Ubuntu server, with Gazebo and protected resources on a Mac. An additional guide describes running the Forklift Agent on a ROSMASTER robot while movement remains in Gazebo.

## Getting started

Clone the repository with its IoTAuth submodule:

```sh
git clone --recurse-submodules https://github.com/asu-kim/multi-agentic-auth.git
cd multi-agentic-auth
```

For an existing checkout, run `git submodule update --init --recursive` from the repository root.

Follow the guides for your setup:

| Guide | Contents |
| --- | --- |
| [Configuration GUI guide](AGENT_GRAPH_GUI.md) | Register entities, define delegation privileges, provision credentials and Auth policies, and troubleshoot Tkinter. |
| [Deployment README](a2a-multi-agent/README.md) | Install agent dependencies, configure models and endpoints, start agents, submit tasks, and inspect results and latency measurements. |
| [Simulator README](a2a-multi-agent/simulation/README.md) | Install Gazebo, configure the Ubuntu–Mac deployment and SSH tunnels, start resources, and run or reset the warehouse experiment. |
| [Robot deployment guide](a2a-multi-agent/simulation/ROBOT_DEPLOYMENT.md) | Run the Forklift Agent on ROSMASTER with the simulator and resources on the Mac. |

Provision Auth and resource permissions before submitting tasks, and keep the agent configuration consistent with the deployed Auth database. The linked guides provide the prerequisites and commands for each component.
