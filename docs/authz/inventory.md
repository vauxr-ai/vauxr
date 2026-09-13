# Route and transport inventory

Both :8765 and :8080 serve the same application and policy. HTTP-only test apps use
the same guard. HEAD on GET resources inherits their authorization. OPTIONS is public
preflight (204, no state changes). Owner bootstrap is separately guarded by the configured HTTPS/origin boundary
and explicit local-console proof; see [owner-v1.md](owner-v1.md).

| HTTP method/path | Policy operation | Boundary |
| --- | --- | --- |
| GET /api/auth/status, /api/auth/session | owner contract v1 | HTTPS boundary; session requires cookie |
| POST /api/auth/claim, /api/auth/save, /api/auth/login | owner contract v1 | HTTPS + exact Origin + JSON + durable rate limits; console claim/save proof or operator verifier |
| POST /api/auth/logout | owner contract v1 | owner session + HTTPS + Origin + CSRF |
| POST /api/enrollment/v1/{action} | enrollment v1 | configured HTTPS; signed client proof or fresh owner/integration control; [exact actions and roles](enrollment-v1.md) |
| GET /api/devices | devices.list | owner/integration |
| PATCH /api/devices/{device_id} | device.configure | owner, including button mapping |
| POST /api/devices/{device_id}/announce | device.announce | owner/integration |
| POST /api/devices/{device_id}/command | device.control; firmware.initiate for ota | owner/integration; validated command allowlist |
| GET /api/channels | channel.list | owner, metadata only |
| POST /api/channels | credential.create | unshipped, owner 501 |
| DELETE /api/channels/{channel_id} | credential.revoke | unshipped, owner 501 |
| POST /api/channels/{channel_id}/activate | channel.configure | owner |
| POST /api/channels/{channel_id}/rotate | credential.rotate | unshipped, owner 501 |
| GET /api/webhooks | webhook.configure | owner, redacted projection |
| POST /api/webhooks | webhook.configure | owner |
| PATCH /api/webhooks/{webhook_id} | webhook.configure | owner |
| DELETE /api/webhooks/{webhook_id} | webhook.configure | owner |
| POST /api/webhooks/{webhook_id}/duplicate | webhook.configure | owner, redacted projection |
| GET /firmware/{filename} | firmware.read | owner/device; .bin confined to DATA_DIR/firmware |
| POST configured REALTIME offer_path (default /api/offer) | realtime.offer | device's own identity and armed/live wake; no pc_id/restart |
| GET /ws upgrade | device message boundary below | upgrade itself gives no device access |
| GET configured channel.ws_path (default /channel) upgrade | integration message boundary below | upgrade itself gives no channel access |
| GET /{tail:.*} | explicit public static boundary | web-client/dist only; /api paths return 404 |
| Any new registered handler without boundary declaration | none | denied by middleware |

No firmware publication/upload, generic configuration,
credential disclosure, or playback URL endpoint currently exists. Their policy
operations are reserved, not aliases for generic server administration.

| Device WS message | Policy operation | Resource |
| --- | --- | --- |
| hello | device.connect | stored subject = device_id |
| voice.start, voice.end | device.audio | bound identity |
| realtime.start | device.audio | bound identity; transport must be enabled |
| abort, realtime.media_ready, realtime.pause, realtime.resume, realtime.stop | device.self_control | bound identity |
| device.button | device.button | bound identity; only owner-configured stored gesture action |
| binary 0x01 microphone/pre-roll | device.audio | current bound socket and identity |
| other text operation | none | deny/close |
| other binary type | none | ignored, no action |

Server-to-device hello/ready/transcript/audio/control are internal consequences of
these authorized ingress operations. A device can trigger its stored button webhook,
prompt, announce or local control, but cannot choose a webhook URL/authorization or
change button mapping through the socket. Webhook HTTP POST is outbound only;
`button_dispatch` reads owner-configured webhooks and never treats external requests
as webhook-configuration authority. HTTP announce and command target selection is
permitted to owner/integration, not to device credentials.

| Channel WS message/path | Policy operation | Constraints |
| --- | --- | --- |
| channel.auth | channel.connect | integration subject references a routing channel; 10s auth timeout |
| channel.response.delta / .end / .error | voice.respond | current socket for active bound channel; existing device listener |
| channel.transcript (server outbound) | internal voice routing | selected current authenticated integration only |
| other client messages | none | denied; never dispatch commands/configuration |
| OpenClaw native outbound WS | internal configured backend | OPENCLAW_TOKEN is not accepted as inbound auth |

Realtime ICE/DTLS-SRTP and media callbacks originate from the identity-authorized
new offer. The peer's device identity comes from the manager's server-side session
binding. The standalone manager method is an internal API, not another HTTP route.
Direct media-plane revocation and secure server trust are dependencies (#49/#45).
There is no permissive bearer fallback on the socket, body-token signaling, firmware,
or inactive-channel response path.
