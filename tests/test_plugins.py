"""The available-plugin catalog distributes only a pinned implemented package."""
import hashlib
import io
import json
import tarfile

from aiohttp.test_utils import TestClient, TestServer

from tests.auth_helpers import owner_headers
from vauxr import plugins
from vauxr.config import reset_config
from vauxr.web.server import make_http_app


def test_pinned_package_manifest_and_bounded_contents():
    archive = plugins.package()
    entry = plugins.catalog()[1]
    assert entry["sha256"] == hashlib.sha256(archive).hexdigest()
    assert entry["revision"] == "d2d90992ee012072758a6742e0177f86bf69eb0e"
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        names = tar.getnames()
        assert len(names) == 17
        assert all(name.startswith("package/") and ".." not in name.split("/") for name in names)
        assert all(name.startswith("package/dist/") or name in {
            "package/LICENSE", "package/README.md", "package/package.json", "package/openclaw.plugin.json",
        } for name in names)
        assert all(not row.issym() and not row.islnk() for row in tar.getmembers())
        package = json.load(tar.extractfile("package/package.json"))
        assert "package/" + package["main"].removeprefix("./") in names
        assert json.load(tar.extractfile("package/openclaw.plugin.json"))["id"] == "vauxr"


async def test_owner_catalog_and_download_boundary(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN", "https://owner.example")
    monkeypatch.setenv("OWNER_TRUSTED_PROXIES", "127.0.0.1/32")
    reset_config()
    try:
        async with TestClient(TestServer(make_http_app())) as client:
            for path in ["/api/plugins", "/api/plugins/openclaw/package"]:
                assert (await client.get(path)).status == 401
            headers = owner_headers(client)
            response = await client.get("/api/plugins", headers=headers)
            assert response.status == 200
            entries = await response.json()
            assert [p["id"] for p in entries] == ["support", "openclaw", "langgraph"]
            archive = await client.get(entries[1]["download"], headers=headers)
            assert archive.status == 200
            assert await archive.read() == plugins.package()
            assert archive.headers["Content-Disposition"].startswith("attachment;")
            assert archive.headers["Cache-Control"] == "no-store"
    finally:
        reset_config()
