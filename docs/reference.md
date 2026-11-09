# Interface reference

This is a lookup map for current boundaries, not a second API implementation.
[Configuration](configuration.md) owns persisted schemas; [README HTTP API](../README.md#http-api)
owns device/channel/webhook route tables. Versioned source schemas below are the
field validators used by the service. Legacy Postman token examples are not
current authorization examples.

## Authority by operation

| Caller | Credential / transport | Scope and limit |
| --- | --- | --- |
| Owner browser | Same-origin HttpOnly session cookie; mutation Origin and CSRF | Management at exact configured authority; not device identity |
| Browser or physical device | Scoped device credential and assigned ID | Own device WS/media and authorized firmware reads |
| Integration | Enrolled `vx_int_` bearer credential | Allowed device/control and own agent operations; not owner management |
| Direct OpenClaw client | Gateway authentication and private signing identity | Separate outbound native gateway connection, not an integration token |
| Firmware-delivery recipient | Single-use URL capability | One exact binary, 120 seconds, one server process |

The operation allowlists are [auth/policy.py](../src/vauxr/auth/policy.py).
Credential currentness is checked separately from registry visibility. A
superseded socket may remain retained until teardown proves successful.

## Versioned provisioning contracts

| Contract | Authoritative validator | Service boundary |
| --- | --- | --- |
| Physical/browser enrollment v1 | [enrollment_schema.py](../src/vauxr/provisioning/enrollment_schema.py) | [enrollment_http.py](../src/vauxr/provisioning/enrollment_http.py) |
| Integration enrollment v1 | [integration_schema.py](../src/vauxr/provisioning/integration_schema.py) | [integration_http.py](../src/vauxr/provisioning/integration_http.py) |
| Rotation/revoke lifecycle v1 | [lifecycle_schema.py](../src/vauxr/provisioning/lifecycle_schema.py) | [lifecycle_http.py](../src/vauxr/provisioning/lifecycle_http.py) |

Enrollment approval, one-time credential delivery, durable save and ACK are
separate transitions. Do not ACK before saving. Exact request fields, proofs and
terminal states belong to these versioned validators, not an informal client
approximation. Shared rate limiting can return 429; wait before retrying. A
lost mutation response is not proof that nothing committed.

## Device and agent wire lookup

| Boundary | Representation / correlation | Authority |
| --- | --- | --- |
| Device `/ws` control | JSON object in a text frame; initial scoped authentication | [protocol.py](../src/vauxr/protocol.py), [server.py](../src/vauxr/server.py) |
| Device binary audio | Type byte + unsigned 16-bit big-endian sequence + PCM; sequence wraps at 65536 | [protocol.py](../src/vauxr/protocol.py) |
| Standard mic | 16 kHz mono signed 16-bit PCM; `voice.start` / `voice.end` capture | [server.py](../src/vauxr/server.py) |
| Reply / announcement | Types `0x02` / `0x03`; output rate signalled separately | [pipeline.py](../src/vauxr/pipeline.py), [buttons.py](../src/vauxr/buttons.py) |
| Physical realtime startup | Nonzero 32-bit `startup_id`; `next_seq` is next 16-bit WS sequence | [startup.py](../src/vauxr/realtime/startup.py) |
| Plugin `/agent` | `agent.auth` → `agent.ready`; device/run or `requestId` correlation | [agents/server.py](../src/vauxr/agents/server.py) |

Names are display metadata, never identity keys. [Architecture](../ARCHITECTURE.md#realtime-cold-to-warm-handoff)
explains WS-to-RTP ordering and why transport readiness differs from provider readiness.

## Resource and timing limits

| Resource | Current implementation limit / semantics |
| --- | --- |
| Startup audio | 15 s retained audio; 20 s setup; separately calculated paced-drain deadline |
| Standard segment queue | Unbounded pending text; serial synthesis; cooperative abort between callbacks |
| Retired connection close | 2 s observation deadline; failed/noncooperative attempts remain retained |
| Realtime prefill | 120 ms default, constructor accepts `(0, 500]` ms; not sustained backpressure |
| Companion context | 128 transient views, 1 h expiry; authority and routing fingerprint rechecked |
| Companion UI delivery | 64 pending events total, 4/device; 10 s receipt timeout |
| Browser text turn | 120 s deadline; 32,000-character response cap; uncertain actions must not be blindly retried |
| Static interface source | Title 120, HTML 20,000, CSS 12,000 characters; exact document revision required for save |

Sources: [output buffer](../src/vauxr/realtime/output_buffer.py),
[connection teardown](../src/vauxr/auth/connections.py),
[Companion](../src/vauxr/companion.py), [chat](../src/vauxr/chat.py),
[interface](../src/vauxr/interface.py). These are resource limits, not latency or
exactly-once external-action promises.

## Browser and static interface contracts

Owner HTTP calls pass through [ownerFetch](../web-client/src/auth/api.ts), which
removes Authorization headers and sends same-origin cookies. Lifecycle client
calls intentionally omit cookies and use their scoped bearer. Browser voice
has a separately persisted identity; logging into the portal is not its enrollment.

The static interface is sanitized HTML/CSS, not a script runner. Saving requires
the exact revision returned by read; conflict means refresh before saving.
Preview does not publish. Resource loading, scripts, forms and frames are blocked
by its sanitizer/CSP. Companion UI receipts report whether the page applied an
action, not conversation continuity or approval for arbitrary tools.
