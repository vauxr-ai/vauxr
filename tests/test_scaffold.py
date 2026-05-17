"""Phase 1 smoke test: server module imports."""

import server


def test_server_imports() -> None:
    assert hasattr(server, "make_app")
