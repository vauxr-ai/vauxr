# Start with Vauxr

Vauxr connects a conversation interface to an agent and speech services. You can
start with local text configuration before adding microphones or speakers.
An **agent type** is an implementation; an **instance** is a named assistant you
configure and select. OpenClaw is an optional external agent, not a prerequisite.

## Choose your next step

- Learn by making something: [Build your first custom agent](tutorial.md).
- Understand selection and ownership: [Agent concepts](concepts.md).
- Get a local model working: [Ollama setup and troubleshooting](guides.md).
- Automate one management task: [HTTP API example](api.md).
- Connect an existing OpenClaw agent: [OpenClaw connection guide](/openclaw/docs/connect).

## Start a disposable development server

Use a fresh source checkout, Python 3.12+, Node.js with npm, and free ports 8080
and 8765. Run these commands from the checkout root on the browser's machine.
This is a development path, not an upgrade procedure for an existing installation.

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e '.[dev]'
npm --prefix web-client ci
npm --prefix web-client run build
mkdir -p .tutorial-data
DATA_DIR="$PWD/.tutorial-data" python3 -m vauxr
```

Expected: the process stays running and serves the portal at
`http://localhost:8080`. Keep this terminal open. The data directory must be
writable. Speech services are not installed by these commands; text setup and
management do not need them.

In a second terminal, activate the same environment, stay at the checkout root,
and run the owner claim against the same data directory:

```sh
. .venv/bin/activate
DATA_DIR="$PWD/.tutorial-data" vauxr-owner claim
```

Open the portal, enter the one-time claim code, save the generated operator token
in a password manager, confirm the save, and log in. Expected: Agents and Settings
load. Keep the token private; it is a recovery/login credential, not plugin config.
Use the exact localhost origin above: changing scheme, hostname or port changes
the owner boundary. This local exercise does not configure remote owner access.

If startup fails, inspect the terminal error and directory permissions before
retrying; do not erase existing data. If login fails, check both terminals use the
same absolute data path and that your browser uses the configured origin.

Stop with Ctrl-C; keep `.tutorial-data` to resume. Do not run this exercise over a
live server's data. Commands are checked against source entrypoints; this page
does not claim a live browser or speech acceptance run.
