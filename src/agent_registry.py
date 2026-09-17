"""Routing-agent registry.

Port of `src/agent-registry.ts`. Agents are stored in `agents.json`
with bcrypt-hashed tokens. The virtual `openclaw-direct` agent exists
when `OPENCLAW_URL` is configured. The active selection persists in
`config.json` so it survives restarts.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import bcrypt

from config import get_config

BCRYPT_COST = 10
TOKEN_PREFIX = "vx_ag_"
TOKEN_HEX_LEN = 64


AgentType = Literal["openclaw", "openclaw-direct"]


@dataclass
class Agent:
    id: str
    name: str
    type: AgentType
    tokenHash: str
    active: bool
    createdAt: str
    builtin: bool | None = None


@dataclass(frozen=True)
class AgentPublic:
    id: str
    name: str
    type: AgentType
    active: bool
    createdAt: str
    builtin: bool | None = None


_agents: list[Agent] = []
_openclaw_direct_active = False
_loaded = False


def _agents_path() -> str:
    return os.path.join(get_config().data_dir, "agents.json")


def _config_path() -> str:
    return os.path.join(get_config().data_dir, "config.json")


def _ensure_data_dir() -> None:
    os.makedirs(get_config().data_dir, exist_ok=True)


def _save_agents() -> None:
    _ensure_data_dir()
    serialized = [
        {
            "id": c.id,
            "name": c.name,
            "type": c.type,
            "tokenHash": c.tokenHash,
            "active": c.active,
            "createdAt": c.createdAt,
            **({"builtin": c.builtin} if c.builtin is not None else {}),
        }
        for c in _agents
    ]
    with open(_agents_path(), "w", encoding="utf-8") as f:
        json.dump(serialized, f, indent=2)


def _save_config() -> None:
    _ensure_data_dir()
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump({"openclawDirectActive": _openclaw_direct_active}, f, indent=2)


def load() -> None:
    """(Re)load agents.json + config.json from disk."""
    global _agents, _openclaw_direct_active, _loaded
    _ensure_data_dir()

    p = _agents_path()
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        _agents = [
            Agent(
                id=str(c.get("id")),
                name=str(c.get("name")),
                type=c.get("type"),
                tokenHash=str(c.get("tokenHash", "")),
                active=bool(c.get("active", False)),
                createdAt=str(c.get("createdAt", "")),
                builtin=c.get("builtin"),
            )
            for c in raw
        ]
    else:
        _agents = []

    cp = _config_path()
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            cfg = json.load(f)
        _openclaw_direct_active = bool(cfg.get("openclawDirectActive", False))
    elif get_config().openclaw.url and not _agents:
        # First-run default: openclaw-direct active when URL configured.
        _openclaw_direct_active = True
        _save_config()
    else:
        _openclaw_direct_active = False

    _loaded = True


def _generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_hex(TOKEN_HEX_LEN // 2)


def _openclaw_direct_agent() -> Agent | None:
    if not get_config().openclaw.url:
        return None
    return Agent(
        id="openclaw-direct",
        name="OpenClaw Direct",
        type="openclaw-direct",
        tokenHash="",
        active=_openclaw_direct_active,
        createdAt=datetime.fromtimestamp(0, tz=UTC).isoformat().replace("+00:00", "Z").replace("Z", ".000Z"),
        builtin=True,
    )


def _public(c: Agent) -> AgentPublic:
    return AgentPublic(
        id=c.id, name=c.name, type=c.type, active=c.active, createdAt=c.createdAt, builtin=c.builtin
    )


def get_all() -> list[AgentPublic]:
    out: list[AgentPublic] = []
    direct = _openclaw_direct_agent()
    if direct is not None:
        out.append(_public(direct))
    for c in _agents:
        out.append(_public(c))
    integrations = _integration_agents()
    if any(c.active for c in integrations):
        from dataclasses import replace

        out = [replace(c, active=False) for c in out]
    out.extend(_public(c) for c in integrations)
    return out


def get_by_id(agent_id: str) -> Agent | None:
    enrolled = next((c for c in _integration_agents() if c.id == agent_id), None)
    if enrolled is not None:
        return enrolled
    if agent_id == "openclaw-direct":
        return _openclaw_direct_agent()
    for c in _agents:
        if c.id == agent_id:
            return c
    return None


def get_active() -> Agent | None:
    enrolled = next((c for c in _integration_agents() if c.active), None)
    if enrolled is not None:
        return enrolled
    direct = _openclaw_direct_agent()
    if direct is not None and direct.active:
        return direct
    for c in _agents:
        if c.active:
            return c
    return None


async def create(name: str, type_: str = "openclaw") -> tuple[AgentPublic, str]:
    if type_ != "openclaw":
        raise ValueError("invalid type, must be 'openclaw'")
    token = _generate_token()
    token_hash = await asyncio.to_thread(
        bcrypt.hashpw, token.encode("utf-8"), bcrypt.gensalt(BCRYPT_COST)
    )
    agent = Agent(
        id=str(uuid.uuid4()),
        name=name,
        type="openclaw",
        tokenHash=token_hash.decode("utf-8"),
        active=False,
        createdAt=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    _agents.append(agent)
    _save_agents()
    return _public(agent), token


def remove(agent_id: str) -> bool:
    global _agents
    if agent_id == "openclaw-direct":
        return False
    before = len(_agents)
    _agents = [c for c in _agents if c.id != agent_id]
    if len(_agents) == before:
        return False
    _save_agents()
    return True


def activate(agent_id: str) -> bool:
    global _openclaw_direct_active
    if get_by_id(agent_id) is None:
        return False
    if _activate_integration(agent_id):
        return True
    if agent_id == "openclaw-direct":
        direct = _openclaw_direct_agent()
        if direct is None:
            return False
        for c in _agents:
            c.active = False
        _save_agents()
        _openclaw_direct_active = True
        _save_config()
        return True

    target = next((c for c in _agents if c.id == agent_id), None)
    if target is None:
        return False
    for c in _agents:
        c.active = False
    _openclaw_direct_active = False
    _save_config()
    target.active = True
    _save_agents()
    return True


async def rotate_token(agent_id: str) -> str | None:
    if agent_id == "openclaw-direct":
        return None
    target = next((c for c in _agents if c.id == agent_id), None)
    if target is None:
        return None
    token = _generate_token()
    target.tokenHash = (
        await asyncio.to_thread(bcrypt.hashpw, token.encode("utf-8"), bcrypt.gensalt(BCRYPT_COST))
    ).decode("utf-8")
    _save_agents()
    return token


async def validate_agent_token(raw_token: str) -> Agent | None:
    # Filter on prefix first: only `vx_ag_` tokens are agent tokens. This
    # also short-circuits the bcrypt path for device tokens and stray Bearer
    # values, which matters because bcrypt.checkpw raises ValueError when the
    # input exceeds bcrypt's 72-byte password limit — without this guard, an
    # oversize Bearer header would surface as a 500 from every @_require_auth
    # endpoint instead of a clean 401.
    if not raw_token.startswith(TOKEN_PREFIX):
        return None
    token_bytes = raw_token.encode("utf-8")
    if len(token_bytes) > 72:
        return None
    for c in _agents:
        try:
            ok = await asyncio.to_thread(
                bcrypt.checkpw, token_bytes, c.tokenHash.encode("utf-8")
            )
        except ValueError:
            # Malformed tokenHash on disk (empty, truncated, hand-edited).
            # Skip the broken entry instead of poisoning every other request.
            continue
        if ok:
            return c
    return None


# --- Test helpers ---


def _reset_for_tests() -> None:
    global _agents, _openclaw_direct_active, _loaded
    _agents = []
    _openclaw_direct_active = False
    _loaded = False


def _set_active_for_tests(agent: Agent | None) -> None:
    """Used in pipeline tests where we don't want disk I/O."""
    global _openclaw_direct_active
    if agent is None:
        for c in _agents:
            c.active = False
        _openclaw_direct_active = False
        return
    if agent.id == "openclaw-direct":
        for c in _agents:
            c.active = False
        _openclaw_direct_active = True
        return
    # Insert if missing so get_active() finds it.
    if not any(c.id == agent.id for c in _agents):
        _agents.append(agent)
    for c in _agents:
        c.active = c.id == agent.id
    _openclaw_direct_active = False


def _integration_agents() -> list[Agent]:
    """Project atomically enrolled routing metadata; never copy credentials to agents.json."""
    from auth import get_store

    store = get_store()
    state = store.integration
    return [Agent(id=row["agent_id"], name=row["display_name"], type="openclaw", tokenHash="",
                    active=state.get("active_agent") == row["agent_id"],
                    createdAt=datetime.fromtimestamp(row["created_at"], tz=UTC).isoformat())
            for row in state.get("requests", {}).values() if store.integration_agent_valid(row)]


def _activate_integration(agent_id: str) -> bool:
    import copy

    from auth import get_store

    store = get_store()
    with store.transaction():
        state = copy.deepcopy(store.integration)
        if not state:
            return False
        found = any(r["agent_id"] == agent_id and store.integration_agent_valid(r)
                    for r in state["requests"].values())
        # A row may have been retired since activate() looked it up.
        if not found and any(r["agent_id"] == agent_id for r in state["requests"].values()):
            return False
        state["active_agent"] = agent_id if found else ""
        store.integration = state
        try:
            store.replace(store.records)
        except BaseException:
            store.load()
            raise
        return found
