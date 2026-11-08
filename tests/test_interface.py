"""Interface source cannot address host files; preview never executes source."""
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests.auth_helpers import owner_headers
from vauxr import interface
from vauxr.config import reset_config
from vauxr.web.server import make_http_app


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN", "https://owner.example")
    monkeypatch.setenv("OWNER_TRUSTED_PROXIES", "127.0.0.1/32")
    reset_config()
    yield
    reset_config()


def test_source_revision_conflict_and_no_arbitrary_files(tmp_path):
    first = interface.read()
    assert first["published"] is False
    body = {"title":"Household", "html":"<h2>Household</h2>", "css":"h2 {color: purple}", "revision":first["revision"]}
    changed = interface.save(body)
    assert changed["published"] is True
    assert changed["revision"] != first["revision"]
    with pytest.raises(ValueError):
        interface.save(body | {"html":"Stale overwrite"})
    with pytest.raises(ValueError):
        interface.save(body | {"path":"/data/authz.json"})
    assert set(p.name for p in tmp_path.iterdir() if p.suffix == ".json") == {"interface.json"}
    assert json.loads((tmp_path / "interface.json").read_text())["html"] == "<h2>Household</h2>"


def test_sanitization_and_css_cannot_escape_document():
    rendered = interface.render({"title":"<unsafe>", "html":'''<h2 onclick="alert(1)">Safe</h2><script>parent.fetch('/api/canary')</script><img src="/api/canary"><meta http-equiv="refresh" content="0;url=/api/canary"><form action="/api/canary"><button>Go</button></form><svg><a href="/api/canary">bad</a></svg><iframe src="/api/canary"></iframe>''', "css":"</style><script>parent.fetch('/api/canary')</script>"})
    assert "<h2>Safe</h2>" in rendered
    assert "onclick=" not in rendered
    for tag in ["<script", "<iframe", "<img", "<form", "<svg"]:
        assert tag not in rendered
    assert "default-src &#x27;none&#x27;" in rendered
    assert "\\3c /style>" in rendered
    assert "<title>&lt;unsafe&gt;</title>" in rendered


async def test_owner_csrf_boundary_and_preview_does_not_publish(tmp_path):
    async with TestClient(TestServer(make_http_app())) as client:
        assert (await client.get("/api/interface")).status == 401
        headers = owner_headers(client)
        current = await (await client.get("/api/interface", headers=headers)).json()
        body = {"title":"Draft", "html":"<p>Draft</p>", "css":""}
        response = await client.post("/api/interface/preview", headers=headers, json=body)
        assert response.status == 200
        assert not (tmp_path / "interface.json").exists()
        no_csrf = {k:v for k,v in headers.items() if k.lower() != "x-csrf-token"}
        assert (await client.post("/api/interface", headers=no_csrf, json=body | {"revision":current["revision"]})).status == 403
        assert (await client.post("/api/interface", headers=headers, json=body | {"revision":current["revision"]})).status == 200
