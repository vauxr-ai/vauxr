"""Explicit local-console owner setup. Never invoked automatically at startup."""

import argparse
import sys

from auth import get_store
from owner_auth import OwnerAuth, OwnerError, configured_origin, environment_token, generate_token


def main() -> None:
    parser = argparse.ArgumentParser(description="Local owner console; run as the server OS account")
    parser.add_argument("command", choices=["generate-token", "claim", "recover"])
    args = parser.parse_args()
    # Secrets must never enter redirected logs/files. No --force/stdout bypass.
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        parser.exit(2, "An interactive private terminal is required; disable terminal/session recording.\n")
    try:
        if args.command == "generate-token":
            print(generate_token())
            return
        origin = configured_origin()
        if not origin:
            raise ValueError("Configure OWNER_ORIGIN before owner setup")
        service = OwnerAuth(get_store(), environment_token())
        # Do not reconcile environment here: only server startup can observe override removal.
        code = service.console_claim(recover=args.command == "recover")
        if origin.startswith("https://"):
            print(f"First establish browser-trusted HTTPS at {origin}; never bypass a certificate warning.")
        else:
            print(f"Use the configured home-network origin {origin}; HTTP transport is unencrypted.")
        print("Enter this single-use claim code within 5 minutes in the trusted setup flow:")
        print(code)
    except (ValueError, OSError, OwnerError) as exc:
        message = str(exc) if isinstance(exc, (ValueError, OwnerError)) else "Owner storage unavailable"
        parser.exit(2, message + "\n")


if __name__ == "__main__":
    main()
