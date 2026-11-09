# Operate and recover

These guides assume a running instance and an authenticated owner portal at its
configured origin. Start with the [tutorial](tutorial.md) if those are unfamiliar.
Refresh state before retrying a mutation whose response was lost: a network error
can occur after a durable write or an external action.

## Change speech without losing the working selection

1. Record the current agent, device assignment, mode, provider and voice. Ensure
   the intended speech service is running at a gateway-reachable endpoint. A
   provider entry does not install a model or start a service.
2. In speech settings, configure the deployment's real model and accepted voice
   IDs. Keep provider editing separate from choosing the active selection.
3. Connect this browser as an enrolled voice device, then preview a voice before changing an agent/device selection. A preview exercises
   synthesis, not STT, agent routing or device playback.
4. Select the intended mode and provider/voice for the agent or device. Only
   supported and enabled modes are usable; a chat-only agent cannot be made
   voice-enabled by a device override.
5. Start a **new** short turn. Standard turns hold immutable selections, so an
   already-running turn is not a valid check of the new setting.

**Expected outcome:** new turns use the selected deployment; unavailable speech
fails explicitly rather than choosing a fallback provider. If synthesis fails,
restore the recorded selection and check the service endpoint and voice ID.
If a change is rejected while active, stop the affected voice session, refresh,
and retry deliberately. To return a device field to inheritance, clear its
override (the API uses `null`); do not duplicate global defaults into every device.
See [configuration](configuration.md#speech-providers-and-selections) for lookup.

## Pair a physical device

1. Use compatible server and firmware revisions. The server-assigned ID is opaque;
   clients must retain it whole, not assume a fixed fingerprint length.
2. Put the device into its supported pairing flow. With guided audio, listen for
   the welcome and short-release the Action button to hear the four-digit code.
   Repeat the short press to hear the **same** code within the existing window.
3. Open **Pairing and access** in the owner portal. Enter the matching code and
   explicitly approve the request. Never approve a request you cannot identify.
4. Allow the device to redeem, save/read back, and acknowledge its credential.
   Approval alone is not the connected-device checkpoint.
5. Check the device inventory and then attempt a short voice turn.

**Expected outcome:** the enrolled identity reconnects using its own credential.
Expiry or denial requires a new request, not repeated approval of the old code.
Audio synthesis failure can fall back to the local USB-console code flow; it does
not waive proof or approval. Avoid rapid polling/retries: provisioning shares a
60-request/minute server budget. See [wire and enrollment reference](reference.md).
The review records [a plugin fixed-length-ID incompatibility](maintainer-review-2026-11-09.md#current-peer-contract-execution-supersedes-historical-fixture-gap);
matching versions must be verified before treating plugin-assisted pairing as accepted.

## Change pairing announcements

In **Pairing and access → Edit pairing messages**, edit the welcome and code
announcement, then save. The welcome must be 1–400 characters without `{code}`;
the code text must be 1–200 characters with `{code}` exactly once. Leading zeros
are spoken individually. New pairing requests use these server-owned prompts.

**Expected outcome:** the saved text remains after refresh. If validation fails,
correct the placeholder/length instead of editing enrollment proofs. If audio is
unavailable, inspect the selected global TTS deployment; do not publish pairing
codes or audio to a public URL. [Architecture](../ARCHITECTURE.md#guided-physical-device-pairing)
owns the audio envelope and synthesis limits.

## Recover an owner-origin mismatch

1. Record the exact URL used by the browser, including external port and scheme.
2. Compare it with the deployment's `OWNER_HTTP_ORIGIN` or `OWNER_HTTPS_ORIGIN`.
   For TLS termination, also verify the explicit trusted proxy configuration.
3. Correct the deployment environment and restart using the same persistent data.
   Follow [README origin/TLS setup](../README.md#browser-on-another-machine-or-address).
4. Reopen that exact origin and complete the appropriate login/claim flow. Confirm
   inventory and a harmless settings read before attempting changes.

**Expected outcome:** owner requests authenticate at the configured authority.
A Host header, shared `DEVICE_TOKEN`, cross-origin Vite server, or turning off
certificate verification is not a recovery method. Do not delete `authz.json` to
resolve an origin mismatch; that changes authority rather than fixing the URL.

## Preserve state before a storage change

Stop all writers to the selected data first and take a private backup preserving
ownership/permissions. Include authorization, routing, speech, device and settings
stores together, plus the separate model/recording/firmware mounts you need.
Follow the [existing volume migration procedure](../README.md#migrating-existing-installations)
when moving from named volumes; ordinary image updates do not need that migration.

**Expected outcome:** settings, enrolled identities and routing survive restart.
If startup rejects malformed configuration, correct the named path/field offline
and restart. Do not restore a single credential record to undo revocation:
tombstones are independent. Restoring an entire old authority snapshot can restore
old authority; a backup is not a revocation-preserving merge. Keep the old storage
until both inventory and a real interaction have been checked.

## Diagnose one failed turn

| Last successful observation | Inspect next | Avoid assuming |
| --- | --- | --- |
| Portal login | Device enrollment/socket status | Owner cookie authorizes device transport |
| Device connected | Capture and STT endpoint | A healthy TCP port proves transcription |
| Transcript received | Selected/assigned agent and peer readiness | Agent metadata proves backend availability |
| Agent text received | TTS deployment/voice and output transport | Preview success proves physical playback |
| Audio sent | Device consumption/playback and follow-up state | A send receipt proves audible output |

Stop/abort before replacing a turn. Cancellation is cooperative and does not
necessarily cancel remote agent/tool work. Check external action status before
retrying an uncertain prompt. Persistent retry, bounded producer queues and
terminal `audio.end` on every STT failure are not universal guarantees; known
limits are collected in the [explanation](../ARCHITECTURE.md).
