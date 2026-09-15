# Owner configuration through Compose

The localhost Quick Start needs no `.env` file or owner environment variables.
With all three owner variables unset, the server uses `http://localhost:8080`.
For a browser on another machine, supply the exact HTTP origin:

```sh
OWNER_HTTP_ORIGIN=http://192.168.1.20:8080 docker compose up -d
```

Compose passes `OWNER_HTTP_ORIGIN`, `OWNER_HTTPS_ORIGIN`, and
`OWNER_TRUSTED_PROXIES` from the host environment (or an optional Compose `.env`)
to the service. Follow [owner setup](../authz/owner-v1.md) for console setup and
verified HTTPS/proxy configuration.

These are bare environment mapping entries intentionally: unset variables have
no container value, while explicitly empty variables remain present and empty.
The presence of either HTTPS or proxy configuration selects TLS, even if its
value is empty. Empty TLS/proxy values are configuration errors; unset them to
return to HTTP. A proxy list requires a valid HTTPS origin. HTTPS takes precedence
over HTTP when selected. Do not replace these entries with `${VARIABLE:-}`,
which would turn missing TLS/proxy settings into empty values and break the
localhost default.

## Offline verification

Run the focused tests with a Compose CLI installed:

```sh
python3 -m pytest -q tests/test_compose_config.py tests/test_config.py
# For a standalone Compose binary:
COMPOSE_BINARY=/path/to/docker-compose python3 -m pytest -q tests/test_compose_config.py tests/test_config.py
```

The render tests use `config --format json`, an empty environment file, and an
isolated host environment; they need no daemon, images, or network. Unresolved
pass-throughs can appear as `null` in Compose's rendered model and are omitted
from the container environment; explicit empty strings are retained. The tests
check both representations and feed the resulting environment to the existing
owner parser. Render tests skip if Compose is unavailable; parser tests still run.
