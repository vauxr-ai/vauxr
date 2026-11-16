# Types, instances and routing

A type describes behavior. An instance is the assistant you name, configure and
route conversations to. Keeping them separate lets two assistants share code
without sharing a model selection or identity.

| Concept | Example | What it owns |
| --- | --- | --- |
| Registered type | `garden` / Garden assistant | Shared prompt, tool policy, framework and supported modes |
| Agent instance | Patio garden helper | Opaque ID, display name, runtime choice and routing identity |
| Model profile | An Ollama connection | Reusable provider configuration, referenced by an instance |
| Default route | One selected agent | Fallback for conversations without an explicit assignment |
| Explicit selection | Browser or device selection | Choice for that conversation/device rather than the fallback |

## From input to answer

The interface identifies the conversation and its selected agent. The server
resolves the instance and its registered implementation or external integration.
A local instance uses the shared LangChain runner and its configured Ollama model;
an OpenClaw integration forwards to the connected external agent. For voice, speech
recognition precedes the agent and speech synthesis follows it.

Changing a display name does not change identity. Creating an instance does not
register a new type. Saving a model configuration does not start the model service.
An integration can be connected without being selected as the route. Check each
boundary independently when a conversation goes somewhere unexpected.

## Manage agents in the portal

In **Agents**, use **+ Add Agent** in the page heading to choose a registered
type or connect OpenClaw. Vauxr Support has its own image in the type catalog.
Use the pencil beside an agent's name to rename it (up to 200 characters);
Enter or the checkmark saves, while Escape or Cancel discards the edit. The
compact editor does not change the agent's identity.

Vauxr Support can be removed like other removable agents. If it is the default,
select another default first, then use its **Delete agent** action and confirm.
Removal persists across server restarts; startup does not reinstall that built-in
instance. The Support type remains available in **+ Add Agent** to create a new
instance with its own identity and configuration.

When connecting an integration, **Deny integration request** opens a confirmation
dialog. **Cancel** leaves the request pending; **Deny request** submits the denial.
If this confirmation is opened inside Add Agent, Escape closes only the
confirmation and returns focus to its trigger, leaving Add Agent open.

## Authority is not routing

The owner session manages server settings. An enrolled integration has scoped
credentials for its permitted operations; it is not an owner session. Browser and
device identities are separate again. Selecting an agent does not grant it owner
rights. The plugin saves its own integration credential after owner approval; do
not paste an owner token into it.

Local custom types currently supply a system prompt and household-tool flag to
one shared runner. They are not arbitrary user-defined frameworks or uploaded
runnables. They use Ollama, not OpenAI model profiles, and support Standard mode.

Try [the custom-agent tutorial](tutorial.md), use [Ollama troubleshooting](guides.md),
or connect [OpenClaw](/openclaw/docs/connect). For one concrete management
contract see [the API example](api.md).
