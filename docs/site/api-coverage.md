# Coverage and verification

Implementation pinned to server `25469df63362853a2a4daf18cc0a115c6626b6bf` and plugin `e0f6c103f904363da431f864c8b39712ee1dc329`.

The ledger inventories **59 registration calls, 63 expanded registrations, 62 unique method/path pairs**. The speech loop expands three paths; the static fallback is registered twice. GET implicitly supports HEAD unless explicitly disabled. Action multiplexers are expanded separately in the ledger: owner (6), enrollment (11), integration (8), lifecycle (7), companion (5).

| Classification | Expanded registrations | Reference |
| --- | ---: | --- |
| HTTP management and binary delivery | 54 | [Control](api-http-control.md), [authentication](api-http-auth.md), [settings](api-http-settings.md) |
| Reserved legacy handlers (501) | 2 | [Control](api-http-control.md) |
| Browser companion multiplexer | 1 | [Settings](api-http-settings.md) |
| WebSocket upgrades and conditional WebRTC signaling | 3 | [Transports](api-transports.md) |
| Static fallback | 2 | [API conventions](api.md#non-api-registrations) |
| OPTIONS preflight | 1 | [API conventions](api.md#non-api-registrations) |

No registered SSE or HTTP audio streaming endpoint exists at this revision. Binary pairing audio, preview WAV and artifact delivery are documented HTTP responses, not event streams. Optional WebRTC signaling is included even when disabled in a deployment. Internal browser contracts and unimplemented legacy endpoints are labeled, not hidden.

Machine-readable evidence: [HTTP ledger](https://github.com/vauxr-ai/vauxr/blob/docs/complete-api-reference/docs/site/api-http-coverage.json), [transport ledger](https://github.com/vauxr-ai/vauxr/blob/docs/complete-api-reference/docs/site/api-transport-coverage.json). Each names inspected implementation, consumer and test paths. Run `python3 scripts/check_api_reference.py` from the source repository to reconcile registered routes against this pinned ledger.

Scope excludes generated/vendor code, provider-internal Wyoming TCP, and unregistered dependency internals. Plugin tools are not additional HTTP listeners: [plugin reference](/openclaw/docs/api/) maps them to the server contracts.

Examples are source-reviewed placeholder requests, not live device or enrollment executions. Source/route reconciliation does not imply a whole-repository code audit. Optional Pipecat runtime and physical media playback were not exercised. No full local browser suite was run.
