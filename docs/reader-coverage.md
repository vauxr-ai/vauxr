# Reader-needs and source coverage

Expansion based on documentation head `ef317cff4a78378b02da2f3ca73cddc97aa446e3`.
The earlier [implementation review](maintainer-review-2026-11-09.md) retains its
own pins and findings. This pass preserves its accurate explanations, adds
learning/task paths, and reconciles narrow public/source contracts. “Sufficient”
below means sufficient for the named reader question, not exhaustive API or
security certification. No subsystem needs a tutorial per module: the tutorial
teaches a whole first success and links into task/reference/explanation homes.

## Topic ownership and gap closure

| First-party subsystem | Existing sufficient material / gap | Substantive reader coverage now | Source/API treatment |
| --- | --- | --- | --- |
| Startup, packaging, Compose, TLS | README installation adequate; no learning checkpoints | Tutorial prerequisites/start/claim/stop; how-to origin/storage recovery; architecture process boundaries | Existing entrypoint/TLS module contracts retained; inspected Compose and manifests, no runtime edits |
| Device WS and protocol | Architecture wire summary adequate; hard to find identity/units | Reference wire table and authority lookup; tutorial device-vs-owner distinction | Existing parser comments retained; registry sequence/ownership contracts added |
| Owner/auth | Existing exact-origin explanation adequate; failure recovery absent | How-to origin mismatch; reference credential table; explanation separation of selection/authority | Retained teardown comments reviewed against implementation; failure/retention semantics explicit |
| Enrollment/integration/lifecycle | Source validators authoritative but undiscoverable | Reference validator/HTTP map; pairing how-to and tutorial extension; durable-save/ACK explanation | Integration metadata projection docstring; existing proof/schema comments retained |
| Guided pairing audio | Architecture envelope adequate; no operator procedure | Pairing and prompt-edit guides, constraints and fallback; reference link to envelope | Existing pairing module/validation comments sufficient; full prompt save/validation boundary inspected |
| Agent routing, Direct, local adapters | Architecture transports and cancellation limits sufficient | Tutorial routing checkpoint; failed-turn guide; explanation metadata vs readiness | Removal partial-progress contract added; registry/Direct existing comments retained |
| Standard pipeline/segmentation | Architecture flow adequate; ownership/cancellation lookup sparse | Reference queue bounds; diagnosis guide and immutable-turn explanation | Queue/idle docstrings reviewed and retained; no backpressure promise |
| Speech providers/adapters | Configuration lacks current agent-policy inheritance | Configuration now covers agent/assignment/capability precedence; practical selection/recovery guide | Selection and resolve snapshot contracts; adapter limits retained in existing explanation |
| Provider settings/secrets | Existing module contract adequate | Reference separates provider readiness from configuration; how-to service checks | Existing SecretStore/public-profile comments retained; no encrypted-at-rest claim |
| Realtime startup/session/transport | Architecture ordered startup adequate | Reference units/IDs/limits; explanation sent vs consumed vs heard | Output-prefill lifetime/epoch docstring; startup-input ordering and teardown workaround already sufficient |
| Realtime display/diagnostics | Existing metrics internals adequate; display vs history unclear | Reference consumption limits; explanation observation boundaries | Transcript relay coalescing/failure/close contracts retained and checked against full body |
| Device config/metrics/firmware | README firmware HTTP contract sufficient | Reference capability lifetime; recovery guide distinguishes device connection/playback | Registry cross-store failure/reconnect comments; firmware consume-before-stream API docstring |
| Buttons/webhooks | README routes and module comments adequate | Failed-turn guide cautions against uncertain-action retries; reference points to button output producer | Existing pre-await prompt lock and replacement-owner checks sufficient; inspected dispatch and announcement body |
| Companion/text/static interface | Modules describe role; no library-level lookup/design account | Reference bounds, revision/receipt semantics; explanation untrusted UI and non-execution | Publish/execute/chat/save/render contracts added with timeout/conflict/partial-effect semantics |
| Plugin settings and bundled definitions | Existing module declaration/distribution distinction adequate | Reference authority map and explanation separate credentials from settings | Registration lifetime/secret-default docstring; package archive excluded |
| Browser owner/device identity, audio and endpointing | Existing auth module comments adequate; sample-count prose drift | Tutorial secure-context caveat; reference cookie vs bearer distinction | ownerFetch stale-401 contract, capture lifetime JSDoc, corrected endpoint duration (200 ms, not chunk count) |
| Browser management components/layout | Existing component names/tests and DESIGN adequate for UI-specific lookup | Tutorial navigation checkpoints, task guides for speech/access/prompts | No decorative component comments; no layout changes or rendering acceptance claim |
| CI/scripts/tests | Existing workflow/source evidence adequate | Tutorial commands checked against manifests; verification below states execution limits | Existing scripts unchanged; focused test evidence, not test-text assertions |

Inventory: `src/vauxr/`, `web-client/src/`, worklet, `scripts/`, build/deployment
manifests and CI ownership follow the previous review's complete source-reading
reconciliation. This expansion reread the edited boundaries and selected callers/
tests, not every body again. It does not claim a fresh exhaustive security audit.
No tracked implementation exists in `firmware/` at this pin. Excluded: dependencies,
generated dist/build, lockfile internals, binary assets, bundled plugin archive,
external plugin/firmware implementations and private/live deployment state.

## Examples for quality review

| Boundary | Before | After / why useful |
| --- | --- | --- |
| Learning entrypoint | Quick Start immediately installs/claims; architecture is next deep link | Tutorial supplies prerequisites, observable checkpoints and a control-plane-only success criterion; hardware is not needed to learn authority |
| Speech task | Configuration enumerates defaults and devices only | How-to records/restores a working selection; configuration now describes agent policy and assignment precedence, so recovery does not flatten inherited values |
| `config_files.write_json` | Bare implementation | Docstring states fsync errors can occur after replace, so failed return does not promise rollback; callers must reread |
| `interface.save` | Revision check visible only inside closure | API contract places locked compare-and-publish and uncertain-write recovery at the caller boundary |
| Browser endpointing | “Two voiced chunks” | “200 ms sustained speech”: the implementation accumulates 3200 samples at 16 kHz, independent of chunk partitioning |

## Verification and limits

- Python 3.12 focused tests: config layout, registry, segment queue, idle segmenter,
  interface, Companion and firmware delivery: **122 passed** (4.85 s).
- Node CI-domain/design-check unit tests: **3 passed**. These are script unit
  checks, not browser design acceptance or a full design gate.
- Changed Python: syntax compilation and docstring-stripped executable AST equality
  against the starting head; changed TypeScript: non-comment token text equality.
- Local Markdown targets and heading anchors, including the new navigation, checked;
  whitespace checked with `git diff --check`. GitHub rendering not visually verified.
- No local full Playwright, live service/device/provider test, deployment, OTA,
  functional fix or merge. No web build: dependencies are absent in this worktree;
  TypeScript changes are comments only. Docker tutorial commands were reconciled
  with source, not executed against a fresh daemon. CI must be read at published head.
