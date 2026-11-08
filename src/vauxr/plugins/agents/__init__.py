"""Validated bundled agent definitions; instances belong in agents.json."""

from __future__ import annotations

import importlib
import re
from importlib.resources import files

from vauxr.config_files import unique_object


def definitions() -> list[dict]:
    import json

    data = json.loads(
        files(__package__).joinpath("definitions.json").read_text(), object_pairs_hook=unique_object
    )
    if (
        not isinstance(data, dict)
        or set(data) != {"version", "definitions"}
        or type(data["version"]) is not int
        or data["version"] != 1
    ):
        raise ValueError("Invalid agent definitions")
    rows = data["definitions"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("Agent definitions required")
    seen = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"id", "name", "framework", "implementation", "supported_modes"}
            or not isinstance(row["id"], str)
            or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", row["id"])
            or row["id"] in seen
            or row["framework"] != "langchain"
            or not isinstance(row["name"], str)
            or not 1 <= len(row["name"]) <= 120
            or not isinstance(row["implementation"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_]*:build", row["implementation"])
            or row["supported_modes"] != ["standard"]
        ):
            raise ValueError("Invalid agent definition")
        seen.add(row["id"])
        module, function = row["implementation"].split(":")
        if not callable(getattr(importlib.import_module(f"{__name__}.{module}"), function, None)):
            raise TypeError("Missing agent implementation")
    return rows


def definition(definition_id: str) -> dict:
    return next(row for row in definitions() if row["id"] == definition_id)


def build(definition_id: str) -> dict:
    row = definition(definition_id)
    module, function = row["implementation"].split(":")
    implementation = getattr(importlib.import_module(f"{__name__}.{module}"), function)()
    if (
        not isinstance(implementation, dict)
        or set(implementation) != {"system", "household"}
        or not isinstance(implementation["system"], str)
        or not 1 <= len(implementation["system"]) <= 8000
        or type(implementation["household"]) is not bool
    ):
        raise ValueError("Invalid registered agent implementation")
    return implementation
