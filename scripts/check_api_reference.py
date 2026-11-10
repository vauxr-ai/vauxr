"""Reconcile all explicit router registrations against the pinned reference ledger."""
import ast
import json
from pathlib import Path
root = Path(__file__).resolve().parents[1]
ledger = json.loads((root / "docs/site/api-http-coverage.json").read_text())
found = []
for path in (root / "src").rglob("*.py"):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"add_get", "add_post", "add_patch", "add_put", "add_delete", "add_route"}:
            found.append((str(path.relative_to(root)), node.lineno))
expected = {(r["source"], r["line"]) for r in ledger["routes"]}
assert set(found) == expected, {"undocumented": sorted(set(found)-expected), "stale": sorted(expected-set(found))}
assert len(found) == ledger["registration_calls"]
assert len(ledger["routes"]) == ledger["expanded_registrations"]
assert len({(r["method"],r["path"]) for r in ledger["routes"]}) == ledger["unique_method_paths"]
for row in ledger["routes"]:
    if row["reference"]:
        assert (root / "docs/site" / row["reference"].split("#")[0]).is_file()
print(f"PASS: {len(found)} registration calls; {len(ledger['routes'])} expanded registrations; no missing registration")
