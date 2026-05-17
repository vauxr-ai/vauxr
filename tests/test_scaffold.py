"""Phase 1 smoke test: the package imports."""

import vauxr


def test_package_version() -> None:
    assert vauxr.__version__.startswith("2.")
