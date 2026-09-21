"""Offline Compose rendering and the existing owner configuration boundary."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from vauxr.auth.owner import configured_origin

ROOT = Path(__file__).resolve().parents[1]
OWNER_KEYS = ("OWNER_HTTP_ORIGIN", "OWNER_HTTPS_ORIGIN", "OWNER_TRUSTED_PROXIES")
HTTP = "http://192.168.1.20:8080"
HTTPS = "https://voice.example"
CASES = [
    pytest.param({}, "http://localhost:8080", id="unset-localhost"),
    pytest.param({"OWNER_HTTP_ORIGIN": HTTP}, HTTP, id="http"),
    pytest.param({"OWNER_HTTPS_ORIGIN": HTTPS}, HTTPS, id="https"),
    pytest.param({"OWNER_HTTP_ORIGIN": HTTP, "OWNER_HTTPS_ORIGIN": HTTPS}, HTTPS, id="https-precedence"),
    pytest.param(
        {"OWNER_HTTPS_ORIGIN": HTTPS, "OWNER_TRUSTED_PROXIES": "127.0.0.1/32, ::1/128"},
        HTTPS, id="https-proxy",
    ),
    pytest.param({"OWNER_TRUSTED_PROXIES": "127.0.0.1/32"}, None, id="proxy-without-https"),
    pytest.param({"OWNER_HTTP_ORIGIN": HTTP, "OWNER_HTTPS_ORIGIN": ""}, None, id="empty-tls"),
    pytest.param({"OWNER_HTTP_ORIGIN": HTTP, "OWNER_TRUSTED_PROXIES": ""}, None, id="empty-proxy"),
    pytest.param({"OWNER_HTTPS_ORIGIN": HTTPS, "OWNER_TRUSTED_PROXIES": ""}, None, id="https-empty-proxy"),
    pytest.param(
        {"OWNER_HTTP_ORIGIN": HTTP, "OWNER_HTTPS_ORIGIN": "", "OWNER_TRUSTED_PROXIES": ""},
        None, id="empty-tls-and-proxy",
    ),
    pytest.param({"OWNER_HTTP_ORIGIN": ""}, None, id="empty-http"),
]


@pytest.fixture(scope="module")
def compose_command() -> list[str]:
    """Use a real Compose CLI; allow a standalone binary without a Docker plugin."""
    binary = os.environ.get("COMPOSE_BINARY")
    if binary:
        command = [str(Path(binary).resolve())]
    elif shutil.which("docker"):
        command = ["docker", "compose"]
    elif shutil.which("docker-compose"):
        command = ["docker-compose"]
    else:
        pytest.skip("Compose CLI required for offline render tests (or set COMPOSE_BINARY)")
    result = subprocess.run([*command, "version"], capture_output=True, text=True, timeout=30, check=False)
    if result.returncode and not binary:
        pytest.skip("Compose plugin unavailable; set COMPOSE_BINARY to a standalone Compose CLI")
    assert result.returncode == 0, result.stderr
    return command


def assert_origin(environment: dict[str, str], expected: str | None) -> None:
    with patch.dict(os.environ, environment, clear=True):
        if expected is None:
            with pytest.raises(ValueError):
                configured_origin()
        else:
            assert configured_origin() == expected


@pytest.mark.parametrize(("host", "expected"), CASES)
def test_owner_config_presence(host: dict[str, str], expected: str | None) -> None:
    """Keep parser coverage runnable even where the optional Compose CLI is absent."""
    assert_origin(host, expected)


@pytest.mark.parametrize(("host", "expected"), CASES)
def test_compose_owner_environment(
    compose_command: list[str], tmp_path: Path, host: dict[str, str], expected: str | None,
) -> None:
    # Ignore both ambient variables and the checkout's .env. A hostile cwd .env
    # also proves that the test exercises the no-.env Quick Start defaults.
    (tmp_path / ".env").write_text("OWNER_HTTPS_ORIGIN=https://unexpected.example\n")
    result = subprocess.run(
        [*compose_command, "--env-file", os.devnull, "-f", str(ROOT / "docker-compose.yml"),
         "config", "--format", "json"],
        cwd=tmp_path,
        env={"PATH": os.defpath, **host},
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    service = json.loads(result.stdout)["services"]["vauxr"]
    rendered = service["environment"]
    for name in OWNER_KEYS:
        if name in host:
            assert rendered[name] == host[name]  # Includes explicit empty strings.
        else:
            # Compose can retain unresolved pass-throughs as null in its model.
            # These do not produce container environment entries.
            assert rendered.get(name) is None
    container_environment = {key: value for key, value in rendered.items() if value is not None}
    assert {key: value for key, value in container_environment.items() if key in OWNER_KEYS} == host
    assert_origin(container_environment, expected)
    assert container_environment["DEVICE_TOKEN"] == ""
    assert "SSL_CERT_FILE" not in container_environment
    assert all("root.crt" not in volume["target"] for volume in service["volumes"])
