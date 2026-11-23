# Tutorial: your first control-plane success

This path teaches the difference between managing Vauxr, selecting an agent,
and transporting voice. You will start a fresh instance, claim owner access,
and inspect its inventory before adding speech or physical hardware. A successful
control-plane session does **not** prove a microphone, provider or speaker works.

## Before you begin

Use a disposable installation, not an existing server's data directory. You need
Git, Python 3.12, Node.js 22, a writable checkout and a browser on the same
machine. Ports 8080 and 8765 must be available. The native install does not
download speech models or require Docker.
You do not need an OpenAI key or an OpenClaw instance for this first success.

Commands below are run at the repository root. Do not copy existing private
`data/` into a tutorial installation.

## 1. Start and observe

Follow [Quick Start](../README.md#quick-start), steps 1–3. Vauxr creates local
`./data` on first run. Watch the server terminal for startup errors.

**Checkpoint:** Vauxr remains running. Open `http://localhost:8080` on the host.
You should reach owner setup/login, not an already-authorized management page.
If startup fails, inspect the terminal output. A permission error points to
`./data`.
Do not delete the directory to clear a startup validation error.

## 2. Claim management authority

Run the [owner claim](../README.md#quick-start) command in a private terminal.
Enter its one-time code in the browser. Save the generated operator token, confirm
the save, and log in. This is your recovery credential; it is not a device token.
Keep it out of screenshots, logs and shell history.

**Checkpoint:** the management portal is available. Reload it: the owner session
is a same-origin cookie, not a token pasted into a voice connection form. If
claim/login fails, compare the browser's full origin (scheme, host, port) with
[the configured owner origin](how-to.md#browser-on-another-machine-or-address).
Do not switch to a Vite URL to bypass the boundary.

## 3. Inspect what exists before creating traffic

Visit Agents, Devices and Settings. Note the available agent inventory and speech
settings without changing them. An agent record is a routing choice; it is not
proof that an external backend is connected. A speech provider listed in Settings
is a deployment description; it is not proof that its service is installed.
A new installation need not have a connected physical device.

**Checkpoint:** navigation and inventory load without an authorization error.
You have completed the control-plane tutorial even if no voice device is online.
The [reference map](reference.md) shows which credential each boundary uses.

## 4. Extend one boundary at a time

Choose one next exercise, rather than changing every layer together:

- Connect OpenClaw using [the plugin instructions](how-to.md#connecting-to-openclaw).
  Approve the displayed enrollment code and let the plugin save and ACK its
  credential. Then select the connected agent.
- Configure and preview a speech deployment using [the task guide](how-to.md#change-speech-without-losing-the-working-selection).
- Pair supported hardware using [the pairing guide](how-to.md#pair-a-physical-device).

For a complete voice checkpoint, speak a short request and observe transcription,
agent response and audible reply in that order. If text arrives without speech,
check TTS before re-enrolling a working agent. Browser microphone permission and
secure-context requirements are separate from successful owner login; use a
browser-trusted HTTPS origin when your browser requires it for capture.

## Finish

Stop the native server with Ctrl-C. Its data remains in `./data` for the next
run. For an existing installation, use the recovery steps in
[Operate and recover](how-to.md), not this fresh-install exercise.

These commands use the current native entrypoints. This tutorial is not a
report of a physical voice-device run on every platform.
