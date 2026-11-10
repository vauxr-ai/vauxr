# Set up and troubleshoot local Ollama

Use this guide when a local agent needs a model, or saved settings produce no
answer. Start with an owner login and a local agent instance; [the tutorial](tutorial.md)
shows how to create a custom one.

## Install a model where Vauxr can reach it

Install Ollama separately using its platform installer. On that machine:

```sh
ollama serve
```

If Ollama already runs as a service, leave it running instead of starting a second
listener. In another terminal:

```sh
ollama pull llama3.2
ollama list
curl http://127.0.0.1:11434/api/tags
```

Expected: `llama3.2` appears in the installed list and the HTTP response lists
models. Download size and hardware needs vary. These are setup examples, not a
claim that this documentation run downloaded or invoked a model.

Test reachability from Vauxr's network environment, not just your laptop.
`127.0.0.1` means the server process's own host/container. If Ollama is elsewhere,
use its reachable private address and restrict access appropriately; do not expose
an unauthenticated Ollama listener to the public Internet.

## Save this instance's runtime

Open Agents, choose the instance's **Runtime settings**, then **Local Ollama
runtime**. Enter `http://127.0.0.1:11434` (or the reachable local address) and the
exact installed model name. Choose **Save agent runtime**, then refresh.

The direct runtime accepts HTTP with an explicit port, a local/private IP or
`.local` / `.lan` hostname, and a root path. Credentials, query, fragment and cloud
model names are rejected. A saved Ollama profile is an alternative: create it in
**Settings → Model providers**, select **Saved model profile** on the instance,
and save. OpenAI profiles are not executed by this local runtime.

Expected: the selection persists. Then explicitly select the instance in the
browser companion and send a short text question. Only an actual reply proves
inference; configured status alone is not a model health check.

## Diagnose one boundary at a time

| Symptom | Check and recovery |
| --- | --- |
| Endpoint rejected on save | Check local address, HTTP scheme, explicit port and root-only URL |
| Connection refused / timeout | Check service listener, firewall and reachability from Vauxr |
| Model unavailable | Compare the exact name with `ollama list`; pull it on the Ollama host |
| Saved profile unavailable | Re-select a valid Ollama profile on this instance |
| OpenAI unsupported | Select Ollama instead; changing the label cannot add runtime support |
| Wrong assistant answers | Check explicit browser/device selection and then default routing |
| Text works, voice fails | Check Standard mode, STT/TTS, browser/device enrollment and playback separately |
| Custom type missing | Validate module and definition, rebuild/restart, then refresh registered agents |

Do not delete data or re-enroll a working integration to fix a model endpoint.
Keep private state and profile credentials out of support reports. See
[agent concepts](concepts.md) for ownership and [the API example](api.md) for
management authentication. To use an external agent instead, follow
[OpenClaw enrollment](/openclaw/docs/connect).
