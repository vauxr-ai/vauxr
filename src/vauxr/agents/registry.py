"""Authoritative routing metadata and selection in agents.json.

Credential and enrollment authority is checked separately in authz.json.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from vauxr.config import get_config
from vauxr.config_files import invalid, read_json, update_json

if TYPE_CHECKING:
    from vauxr.auth.store import CredentialStore

AgentType = Literal["openclaw", "openclaw-direct"]


@dataclass
class Agent:
    id: str
    name: str
    type: AgentType
    active: bool
    createdAt: str
    builtin: bool | None = None
    integration: bool = False


AgentPublic = Agent
_agents: list[Agent] = []
_active_agent = ""
_loaded = False
_directory: Path | None = None


def _path() -> Path:
    return Path(get_config().data_dir) / "agents.json"


def _decode(data: object, path: Path) -> tuple[list[Agent], str]:
    if (
        not isinstance(data, dict)
        or set(data) != {"version", "agents", "active_agent"}
        or type(data["version"]) is not int
        or data["version"] != 1
    ):
        raise invalid(
            path, "$", "expected version 1, agents array and active_agent; recreate legacy configuration"
        )
    if not isinstance(data["agents"], list) or not isinstance(data["active_agent"], str):
        raise invalid(path, "agents/active_agent", "expected an array and a string")
    agents = []
    for index, row in enumerate(data["agents"]):
        field = f"agents[{index}]"
        if (
            not isinstance(row, dict)
            or not {"id", "name", "type", "createdAt"} <= row.keys()
            or row.keys() - {"id", "name", "type", "createdAt", "builtin", "integration"}
        ):
            raise invalid(
                path,
                field,
                "expected id, name, type, createdAt and optional builtin/integration; credentials belong in authz.json",
            )
        if (
            not all(isinstance(row[key], str) and row[key] for key in ("id", "name", "createdAt"))
            or row["type"] not in ("openclaw", "openclaw-direct")
            or type(row.get("integration", False)) is not bool
            or ("builtin" in row and type(row["builtin"]) is not bool)
        ):
            raise invalid(path, field, "expected nonempty strings, a supported agent type and boolean flags")
        if (row["type"] == "openclaw-direct") != (row["id"] == "openclaw-direct") or (
            row["type"] == "openclaw-direct" and (not row.get("builtin") or row.get("integration"))
        ):
            raise invalid(path, field, "OpenClaw Direct must use its builtin ID and type")
        agents.append(Agent(**row, active=row["id"] == data["active_agent"]))
    ids = {a.id for a in agents}
    if len(ids) != len(agents) or (data["active_agent"] and data["active_agent"] not in ids):
        raise invalid(
            path, "active_agent/agents", "IDs must be unique and selection must reference an existing agent"
        )
    return agents, data["active_agent"]


def _encode(agents: list[Agent], active: str) -> dict:
    return {
        "version": 1,
        "agents": [
            {key: value for key, value in asdict(a).items() if key != "active" and value is not None}
            for a in agents
        ],
        "active_agent": active,
    }


def _publish(data: dict) -> None:
    global _agents, _active_agent, _loaded, _directory
    _agents, _active_agent = _decode(data, _path())
    _loaded, _directory = True, _path().parent


def load() -> None:
    global _loaded, _agents, _active_agent
    _loaded, _agents, _active_agent = False, [], ""
    path = _path()
    if path.exists():
        data = read_json(path)
        agents, active = _decode(data, path)
        if get_config().openclaw.url and not any(a.id == "openclaw-direct" for a in agents):
            direct = Agent(
                "openclaw-direct",
                "OpenClaw Direct",
                "openclaw-direct",
                False,
                datetime.fromtimestamp(0, tz=UTC).isoformat(),
                True,
            )
            data = update_json(
                path,
                lambda current: _encode(
                    [*_decode(current, path)[0], direct], active or (direct.id if not agents else "")
                ),
            )
    else:
        direct = Agent(
            "openclaw-direct",
            "OpenClaw Direct",
            "openclaw-direct",
            True,
            datetime.fromtimestamp(0, tz=UTC).isoformat(),
            True,
        )
        data = _encode(
            [direct] if get_config().openclaw.url else [], direct.id if get_config().openclaw.url else ""
        )
        data = update_json(path, lambda existing: existing or data)
    _publish(data)


def validate_authority() -> None:
    """Report missing routing metadata; never rebuild it from enrollment proofs."""
    from vauxr.auth.service import get_store

    store = get_store()
    expected = {
        row["agent_id"]
        for row in store.integration.get("requests", {}).values()
        if store.integration_agent_valid(row)
    }
    recorded = {a.id for a in _agents if a.integration}
    if expected - recorded:
        raise invalid(
            _path(),
            "agents",
            "missing enrolled integration metadata; explicitly reconfigure or reenroll the integration",
        )
    if _active_agent and get_active() is None:
        raise invalid(
            _path(),
            "active_agent",
            "selected agent is unavailable; configure its connection or select an available agent",
        )


def _ensure_loaded() -> None:
    if not _loaded or _directory != _path().parent:
        load()


def _valid(agent: Agent) -> bool:
    if agent.type == "openclaw-direct":
        return bool(get_config().openclaw.url)
    if not agent.integration:
        return True
    from vauxr.auth.service import get_store

    store = get_store()
    return any(
        row["agent_id"] == agent.id and store.integration_agent_valid(row)
        for row in store.integration.get("requests", {}).values()
    )


def get_all() -> list[AgentPublic]:
    _ensure_loaded()
    return [replace(a, active=a.id == _active_agent) for a in _agents if _valid(a)]


def get_by_id(agent_id: str) -> Agent | None:
    return next((a for a in get_all() if a.id == agent_id), None)


def get_active() -> Agent | None:
    return next((a for a in get_all() if a.active), None)


def _commit(change: Callable[[dict], dict]) -> None:
    try:
        data = update_json(_path(), change)
    except OSError:
        # Rename may have committed before directory fsync failed.
        _publish(read_json(_path()))
        raise
    _publish(data)


def register(agent: Agent) -> None:
    """Persist routing metadata after durable enrollment; never accept credentials."""
    _ensure_loaded()

    def change(data: dict) -> dict:
        agents, active = _decode(data, _path())
        existing = next((a for a in agents if a.id == agent.id), None)
        if existing:
            return data  # Enrollment ACK retries must not reset selection/name.
        return _encode([*agents, replace(agent, active=False)], active)

    _commit(change)


def activate(agent_id: str) -> bool:
    if get_by_id(agent_id) is None:
        return False

    def change(data: dict) -> dict:
        agents, _ = _decode(data, _path())
        target = next((a for a in agents if a.id == agent_id), None)
        if target is None or not _valid(target):
            return data
        return _encode(agents, agent_id)

    _commit(change)
    return _active_agent == agent_id


def remove(agent_id: str) -> bool:
    """Remove ordinary routing metadata; builtin agents cannot be deleted."""
    agent = get_by_id(agent_id)
    if agent is None or agent.builtin:
        return False

    def change(data: dict) -> dict:
        agents, active = _decode(data, _path())
        return _encode([a for a in agents if a.id != agent_id], "" if active == agent_id else active)

    _commit(change)
    return True


def retire_invalid(store: CredentialStore) -> None:
    """Credential retirement gates routing immediately, then clears stale selection."""
    path = store.path.parent / "agents.json"
    if not path.exists():
        return

    def change(data: dict) -> dict:
        agents, active = _decode(data, path)
        valid = {
            row["agent_id"]
            for row in store.integration.get("requests", {}).values()
            if store.integration_agent_valid(row)
        }
        retained = [a for a in agents if not a.integration or a.id in valid]
        return _encode(retained, active if any(a.id == active for a in retained) else "")

    data = update_json(path, change)
    if path == _path():
        _publish(data)


def _reset_for_tests() -> None:
    global _agents, _active_agent, _loaded, _directory
    _agents, _active_agent, _loaded, _directory = [], "", False, None


def _set_active_for_tests(agent: Agent | None) -> None:
    _ensure_loaded()
    if agent:
        if not agent.createdAt:
            agent = replace(agent, createdAt=datetime.now(UTC).isoformat())
        register(agent)
        activate(agent.id)
    else:
        _publish(update_json(_path(), lambda data: {**data, "active_agent": ""}))
