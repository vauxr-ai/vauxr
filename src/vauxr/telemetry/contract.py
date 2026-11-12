"""Allowlisted v1 wire projection; raw runtime/config values never enter reports."""
from __future__ import annotations

import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

POLICY = json.loads(Path(__file__).with_name('v1.policy.json').read_text())
SCHEMA = json.loads(Path(__file__).with_name('v1.schema.json').read_text())
LIMIT = 1_000_000


def timestamp(value: float) -> str:
    return datetime.fromtimestamp(int(value), UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def epoch(value: str) -> int:
    return int(datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC).timestamp())


def metrics() -> dict:
    spec = SCHEMA['oneOf'][1]['properties']['metrics']['properties']
    return {k: {name: 0 for name in v['properties']} if v['type'] == 'object' else 0
            for k, v in spec.items()}


def categories(provider: str, model: str, endpoint: str) -> tuple[str, str]:
    # Only the fixed public OpenAI authority has known model semantics. Local,
    # compatible and custom endpoints cannot self-assert an official category.
    known = provider in ('openai', 'openai-live') and endpoint == 'https://api.openai.com'
    p = 'openai' if known else 'custom'
    m = model if known and model in POLICY['model_categories'] else 'custom'
    return p, m


def released_version() -> str:
    try:
        build = json.loads(Path(__file__).with_name('release.json').read_text())
        version = build.get('version')
        return version if build.get('source') == 'github-release' and version in POLICY['released_versions'] else 'custom'
    except (OSError, ValueError, AttributeError):
        return 'custom'


def context(device_count: int = 0) -> dict:
    os_name = platform.system().lower()
    arch = platform.machine().lower()
    return {'release': released_version(),
            'os': os_name if os_name in ('linux', 'darwin', 'windows') else 'other',
            'arch': {'amd64': 'x86_64', 'arm64': 'aarch64', 'armv7l': 'arm'}.get(
                arch, arch if arch in ('x86_64', 'aarch64', 'arm') else 'other'),
            'devices': '0' if device_count == 0 else '1' if device_count == 1 else
            '2-5' if device_count <= 5 else '6-20' if device_count <= 20 else '21+'}


def _validate(value: object, spec: dict) -> None:
    kind = spec.get('type')
    if kind == 'object':
        if type(value) is not dict or set(value) != set(spec['properties']):
            raise ValueError('Invalid telemetry fields')
        for k, child in spec['properties'].items():
            _validate(value[k], child)
    elif kind == 'integer':
        if type(value) is not int or not spec.get('minimum', 0) <= value <= spec.get('maximum', LIMIT):
            raise ValueError('Invalid telemetry integer')
    elif kind == 'string':
        if type(value) is not str:
            raise ValueError('Invalid telemetry string')
        if spec.get('format') == 'date-time' and timestamp(epoch(value)) != value:
            raise ValueError('Invalid UTC timestamp')
        if 'pattern' in spec:
            import re
            if not re.fullmatch(spec['pattern'], value):
                raise ValueError('Invalid telemetry category')
    if 'enum' in spec and value not in spec['enum']:
        raise ValueError('Invalid telemetry category')
    if 'const' in spec and value != spec['const']:
        raise ValueError('Invalid telemetry constant')


def serialize(body: dict) -> bytes:
    """The preview and HTTP sender both use these exact bytes."""
    _validate(body, SCHEMA['oneOf'][0 if body.get('kind') == 'startup' else 1])
    for key in ('report_id', 'monthly_id'):
        if UUID(body[key]).version != 4:
            raise ValueError('Invalid random identity')
    start = body.get('interval_start', body.get('started_at'))
    end = body.get('interval_end', start)
    if body['month'] != start[:7] or epoch(body['created_at']) < epoch(end):
        raise ValueError('Invalid telemetry time')
    if body['kind'] == 'interval':
        duration = epoch(end) - epoch(start)
        m = body['metrics']
        # Intervals are half-open: an exact hour-end still closes the preceding UTC bucket.
        if (not 0 < duration <= 3600 or start[:13] != timestamp(epoch(end) - 1)[:13]
                or not 0 <= m['uptime_ms'] <= duration * 1000
                or sum(m['outcomes'].values()) != sum(m['latency_ms'].values())
                or sum(m['outcomes'].values()) > LIMIT):
            raise ValueError('Invalid interval accounting')
    raw = json.dumps(body, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()
    if len(raw) > 8192:
        raise ValueError('Telemetry payload too large')
    return raw
