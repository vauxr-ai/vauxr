# Owner authentication and provisioning

[HTTP conventions](api.md). Source revision
`25469df63362853a2a4daf18cc0a115c6626b6bf`. Provisioning routes are versioned,
strict object contracts: no query string, duplicate JSON keys or extra/missing
fields. Requests use application/json, at most 4096 bytes within 10 seconds.
All require the configured authority and transport; an Origin, if present, must
match exactly. Owner-cookie mutations additionally require Origin and CSRF.
Do not send cookies and Authorization together. Responses use no-store and
no-referrer. Success is 200 unless stated otherwise. Timestamps are Unix seconds.

## Owner sessions: /api/auth/{action}

GET implicitly supports HEAD. No queries. POST rate-limited; strict body fields,
application/json, 4096 bytes/10 seconds. Initial claim/login do not require an
existing session or CSRF, but do require exact Origin and configured authority.

| Method/action | Exact body | Success schema |
| --- | --- | --- |
| GET status | None | `{version:1,state:string,environment_managed:boolean}` |
| GET session | None; session cookie required | `{version:1,csrf_token:string,expires_at:number}` |
| POST claim | `{code:string}` from authorized console setup | `{version:1,operator_token:string,save_acknowledgement:string,expires_in:300,save_required:true}` |
| POST save | `{save_acknowledgement:string,saved:true}` | `{version:1,state:"generated"}` |
| POST login | `{operator_token:string}` | `{version:1,csrf_token:string,expires_at:number}` plus session cookie |
| POST logout | `{}`; session + CSRF | `{version:1,logged_out:true}` and cookie deletion |

Login sessions last 30 days. Claim is a console-initiated bounded setup/recovery
flow, not an anonymous public owner-registration API. Save the one-time token
securely before acknowledging; the response is the disclosure point. Status
states follow the owner store (`unclaimed`, `generated`, `environment`, `recovery`). GET unknown action is 404
`not_found`; POST unknown action is 400 `invalid_request`. Session missing/expired
is 401 `unauthorized`. Other owner errors use `{error:string}`, 400 (e.g.
`invalid_claim`, `invalid_request`), 429 `rate_limited`, 403 boundary failure,
503 `owner_unavailable`. Example authenticated logout body: `{}`.

## Device enrollment: POST /api/enrollment/v1/{action}

Physical enrollment uses Ed25519 possession plus a human four-digit-code step;
it is not hardware attestation. Browser request/initiate/approve/deny requires
owner. Physical control/list accepts owner or integration. Client actions
`request,prove,redeem,cancel,status` reject Authorization; browser request can
use the owner cookie. Signed actions use key possession instead of bearer auth.

| Action | Exact body | Success |
| --- | --- | --- |
| request | `{kind:"physical"|"browser",public_key:string,display_name:string}` | Binding object below |
| prove | `{request_id,signature}` | `{version:1,status:"ready",code:string,expires_at:number}` or negotiated pairing audio |
| initiate | `{request_id,code}` | `{status:"initiated",device_id}` |
| approve | `{request_id,code}` | `{status:"approved",device_id}` |
| redeem | `{request_id,signature}` | `{version:1,status:"consumed",device_id,credential_id,device_token}` |
| status | `{request_id,signature}` | Public request object below |
| cancel | `{request_id,signature}` | `{status:"cancelled",device_id}` |
| deny | `{request_id}` | `{status:"denied",device_id}` |
| list | `{}` | `{version:1,requests:PublicRequest[]}` |
| prompts | `{}`; owner only | `{intro:string,code:string}` |
| save-prompts | `{intro:string,code:string}`; owner only | Saved prompt object |

`public_key` is 64 lowercase hex characters; request IDs are 32 lowercase hex;
signatures are 128 lowercase hex. Display name matches `[A-Za-z0-9 _.-]{1,64}`.
Code is exactly four decimal digits. Binding response fields, in signing order:
`version,request_id,server_id,origin,kind,public_key,device_id,nonce,expires_at,
owner_generation,display_name`. Version is 1, nonce is 64 hex, server and owner
generation IDs are 32 hex. Sign UTF-8/ASCII compact JSON array
`["vauxr-enrollment",action,...binding values in that order]`, with ensure_ascii,
no spaces; never reconstruct from unordered values. The fixture
`tests/fixtures/enrollment-v1-vector.json` is authoritative for serialization.

PublicRequest is `{request_id,device_id,kind,display_name,status,expires_at}`.
Statuses include challenge → ready → initiated → approved → consumed, or denied,
cancelled, failed, expired, stale. Requests expire after 300 seconds; capacity 64.
Redeem discloses device_token once. Recovery redemption additionally returns
`operation_id,save_required:true`; persist and acknowledge through lifecycle.

`Accept: application/vnd.vauxr.pairing-audio` on prove negotiates `VPA1` bytes:
magic, uint32 little-endian JSON length, uint32 little-endian intro length,
UTF-8 JSON, intro PCM then code PCM. Audio is mono S16LE/16 kHz. Metadata adds
`guided:true,intro_text`. Unavailable/busy synthesis falls back to JSON with that
metadata; clients must handle either content type. Prompt intro is 1–400 trimmed
characters, code message 1–200; `{code}` must occur exactly once in code message,
never intro. Only newline/tab controls allowed. Example save-prompts:
`{"intro":"Ready to pair.","code":"Your pairing code is {code}."}`.

Errors: `{error:code}`. 400 invalid_request/signature/code and other validation;
401 unauthorized; 403 forbidden; 404 not_found; 409 already_owned/conflict/
unavailable; 429 rate_limited/capacity; 503 enrollment_unavailable. Do not retry
one-time redemption blindly after an uncertain response.

## Integration enrollment: POST /api/integrations/v1/{action}

Software enrollment is owner-approved, not acoustic/device pairing. No action
accepts Authorization. Client actions must not send an owner cookie. Owner
`list,approve,deny` requires current owner session and CSRF.

Let `P` mean the two *top-level* required fields `request_id` (32 lowercase hex)
and `request_secret` (64 lowercase hex, client-generated and kept private).

| Action | Exact body fields | Success |
| --- | --- | --- |
| request | P + `origin,display_name,expires_at` | PublicIntegration + `user_code:string` |
| status | P | PublicIntegration |
| deliver | P | PublicIntegration + `credential,credential_id,save_required:true` |
| ack | P + `credential:string,saved:true` | PublicIntegration, completed |
| cancel | P | PublicIntegration, cancelled |
| list | None (`{}`) | `{version:1,requests:PublicIntegration[]}` |
| approve | `request_id,user_code` | PublicIntegration, approved |
| deny | `request_id` | PublicIntegration, denied |

PublicIntegration: `{version:1,request_id,server_id,origin,agent_id,display_name,
expires_at,state}`. IDs/origin/name are strings; expiry is numeric. State is
pending/approved/delivered/completed/denied/cancelled/failed/expired/stale/revoked.
Origin must exactly match configured origin. Request expiry is an integer in
(now, now+300]. Name matches `[A-Za-z0-9][A-Za-z0-9 ._()-]{0,63}`. User code is
8 characters, generated from request binding; do not substitute a four-digit
physical code. Approve permits five failed code attempts before failed state.
Request retry must retain identical bound fields or yields conflict. Delivery is
one-time; credential remains disabled until durable-save ack succeeds. Ack
requires the delivered credential (maximum 512 characters), not just its ID.
Example owner approval: `{"request_id":"<32-lowercase-hex>","user_code":"<8-character-code>"}`.

Errors: 400 invalid_request/invalid_code/invalid_ack; 401 unauthorized;
403 forbidden; 404 not_found; 409 conflict/unavailable; 429 rate_limited/capacity;
503 integration_unavailable. Store credentials before ACK; never print them.

## Credential lifecycle: POST /api/lifecycle/v1/{action}

Owner cookie or current device/integration bearer, never both. Owner bearer is
not accepted. Every non-poll operation_id is 32 lowercase hex, supplied by client.

| Action | Exact body | Authority / success |
| --- | --- | --- |
| rotate | `{operation_id,role,subject}` | Owner; Operation |
| revoke | `{operation_id,role,subject}` | Owner; Operation |
| recover | `{operation_id,role,subject}` | Owner, device only; Operation |
| poll | `{}` | Device/integration; Operation or `{version:1,state:"idle"}` |
| status | `{operation_id}` | Matching subject or owner; Operation |
| deliver | `{operation_id}` | Matching subject, pending rotation; Operation + `credential,save_required:true` |
| ack | `{operation_id,saved:true}` | Matching subject using newly issued credential; Operation |

Role is `device|integration`, subject is the exact device/agent ID. Operation:
`{version:1,operation_id,role,subject,action,state,expires_at,overlap_until,
credential_id}`. IDs/action/state are strings; deadlines numeric. States are queued, pending,
delivered, acknowledged, completed, expired, revoked. Rotation queues expire
after 86400 seconds; delivery overlap is at most 300 seconds, bounded by expiry;
recovery enrollment has a 300-second window. Poll moves
queued rotation to pending; deliver moves to delivered; ACK retires old
credentials and records acknowledged. Recovery requires key-bound enrollment
rather than delivering a credential directly to owner. Reuse an operation ID only
for the same bound operation. Example owner rotate:
`{"operation_id":"<32-lowercase-hex>","role":"device","subject":"<device-id>"}`.
Errors: 400 invalid_request/invalid_ack; 401 unauthorized; 403 forbidden;
404 not_found; 409 conflict/unavailable/recovery_unavailable; 429 rate_limited/
capacity; 503 lifecycle_unavailable or transport_teardown_unavailable. A teardown
failure can follow a durable state change: inspect status before retrying.
