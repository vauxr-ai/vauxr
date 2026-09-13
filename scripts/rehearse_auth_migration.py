"""Offline synthetic rehearsal only: no caller data paths, credentials or sockets.

Run with Python 3.12 and the core project dependencies. The isolated child uses
production storage/auth services, not CLI terminal bypasses or live HTTP routes.
"""

import os
import subprocess
import sys
from pathlib import Path


def check(condition: bool, label: str) -> None:
    if not condition:
        raise RuntimeError(label)  # Fixed labels only; never include fixture secrets.


def rehearse() -> None:
    import json
    import secrets
    import shutil
    import stat
    import tempfile
    import time

    # Also sanitize when the caller already used python3 -I.
    os.environ.clear()
    os.environ.update(PATH=os.defpath, LANG="C.UTF-8")
    sys.dont_write_bytecode = True

    def offline_only(event: str, args: tuple[object, ...]) -> None:
        if event.startswith("socket."):
            raise RuntimeError("network forbidden in disposable rehearsal")

    sys.addaudithook(offline_only)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from auth_policy import Principal
    from auth_store import CredentialStore, atomic_private_json
    from enrollment import Enrollment
    from integration import Integration
    from integration_schema import empty_state as integration_state
    from lifecycle import Lifecycle
    from lifecycle_schema import empty_state as lifecycle_state
    from owner_auth import OwnerAuth, OwnerError, generate_token

    origin = "http://localhost:8080"
    os.umask(0o077)

    def snapshot(directory: Path) -> dict[str, tuple[int, bytes | None]]:
        result: dict[str, tuple[int, bytes | None]] = {}
        for path in [directory, *sorted(directory.rglob("*"))]:
            check(not path.is_symlink(), "unexpected fixture symlink")
            mode = stat.S_IMODE(path.stat().st_mode)
            check(mode == (0o700 if path.is_dir() else 0o600), "fixture privacy")
            result[str(path.relative_to(directory))] = (mode, None if path.is_dir() else path.read_bytes())
        return result

    def backup(source: Path, target: Path) -> None:
        check(not target.exists(), "backup destination must be new")
        shutil.copytree(source, target, copy_function=shutil.copy2)
        # Flush the copied files and directories before verifying. Source has no
        # concurrent writers; real installation-wide quiescence is an operator duty.
        for path in [*target.rglob("*"), target, target.parent]:
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        check(snapshot(source) == snapshot(target), "backup bytes/modes differ")

    def start(directory: Path, override: str | None = None) -> OwnerAuth:
        # Re-open from disk for each simulated process restart. These are the
        # auth startup services, without binding listeners or starting providers.
        os.environ["DATA_DIR"] = str(directory)
        store = CredentialStore(directory / "authz.json")
        owner = OwnerAuth(store, override, origin=origin)
        owner.initialize()
        Enrollment(store, origin).initialize()
        Lifecycle(store, origin).sweep()
        Integration(store, origin).sweep()
        return owner

    def claim(owner: OwnerAuth, recover: bool = False) -> str:
        reply = owner.claim(owner.console_claim(recover=recover))
        denied(owner, reply["operator_token"])
        owner.acknowledge(reply["save_acknowledgement"], True)
        return reply["operator_token"]

    def denied(owner: OwnerAuth, token: str) -> None:
        try:
            owner.login(token)
        except OwnerError:
            return
        raise RuntimeError("retired or pending login accepted")

    # Explicit /tmp ignores ambient TMPDIR. No source/target argument exists.
    with tempfile.TemporaryDirectory(prefix="vauxr-migration-52-", dir="/tmp") as temporary:
        root = Path(temporary)
        for version in range(6):
            case = root / f"schema-{version}"
            case.mkdir()
            data = case / "data"
            data.mkdir()
            settings = {
                "devices.json": {"legacy-kitchen": {"name": "Kitchen", "voice": True}},
                "speech-settings.json": {"defaults": {"stt_backend": "whisper", "tts_backend": "piper",
                                                     "voices": {"piper": "en_US-libritts_r-medium"}},
                                         "devices": {"legacy-kitchen": {"stt_backend": "whisper"}}},
                "speech-providers.json": [],
                "config.json": {"openclawDirectActive": False},
                "channels.json": [],
                "webhooks.json": [],
            }
            for name, value in settings.items():
                atomic_private_json(data / name, value)
            original_settings = {name: (data / name).read_bytes() for name in settings}
            # Minimal valid earlier-schema fixtures, not exported user data or
            # old-binary writers. Version 0 represents no authz file at all.
            if version:
                payload = {"version": version, "credentials": []}
                if version >= 2:
                    payload["owner"] = {"version": 1, "mode": "unclaimed",
                                        "generation": secrets.token_hex(16)}
                if version >= 3:
                    payload["enrollment"] = {"version": 1, "server_id": secrets.token_hex(16),
                                             "requests": {}, "attempts": []}
                if version >= 4:
                    payload["lifecycle"] = lifecycle_state()
                if version >= 5:
                    payload["integration"] = integration_state()
                atomic_private_json(data / "authz.json", payload)
            before = case / "before"
            backup(data, before)
            before_bytes = snapshot(before)

            owner = start(data)
            generated = claim(owner)
            cookie, _ = owner.login(generated)
            check(owner.session(cookie) is not None, "generated session")
            owner = start(data)
            check(owner.session(cookie) is None, "restart session invalidation")
            owner.login(generated)
            override = generate_token()
            owner = start(data, override)
            denied(owner, generated)
            cookie, _ = owner.login(override)
            generation = owner.store.owner["generation"]
            owner = start(data, override)
            check(owner.store.owner["generation"] == generation, "unchanged override generation")
            check(owner.session(cookie) is None, "unchanged override restart session")
            replacement = generate_token()
            owner = start(data, replacement)
            check(owner.store.owner["generation"] != generation, "changed override generation")
            denied(owner, override)
            owner.login(replacement)
            owner = start(data)
            check(owner.status()["state"] == "recovery", "override removal recovery")
            denied(owner, generated)
            denied(owner, replacement)
            recovered = claim(owner, recover=True)
            cookie, _ = owner.login(recovered)
            def resolve(current_owner: OwnerAuth = owner, current_cookie: str = cookie) -> Principal | None:
                session = current_owner.session(current_cookie)
                check(session is not None, "owner session required")
                return session[0] if session else None

            service = Integration(owner.store, origin)
            request = {"request_id": secrets.token_hex(16), "request_secret": secrets.token_hex(32),
                       "origin": origin, "display_name": "Disposable integration",
                       "expires_at": int(time.time()) + 300}
            row = service.execute("request", request)
            service.execute("approve", {"request_id": row["request_id"], "user_code": row["user_code"]},
                            resolve)
            private = {key: request[key] for key in ("request_id", "request_secret")}
            delivery = service.execute("deliver", private)
            check(owner.store.authenticate(delivery["credential"]) is None, "no grant before ACK")
            client_path = case / "client.json"
            atomic_private_json(client_path, {**private, "credential": delivery["credential"]})
            saved = json.loads(client_path.read_text())
            check(saved == {**private, "credential": delivery["credential"]}, "client readback")
            service.execute("ack", {**saved, "saved": True})
            check(owner.store.authenticate(saved["credential"]) is not None, "integration enabled")
            check(json.loads((data / "authz.json").read_text())["version"] == 5, "schema 5 written")
            check(owner.store.authenticate("disposable-legacy-token") is None, "no shared token import")
            check(owner.store.authenticate("vx_ch_disposable") is None, "no legacy channel import")
            check(owner.store.authenticate(recovered) is None, "owner is not client bearer")

            pre_revoke = case / "pre-revoke"
            backup(data, pre_revoke)
            Lifecycle(owner.store, origin).execute("revoke", {
                "operation_id": secrets.token_hex(16), "role": "integration", "subject": row["channel_id"],
            }, resolve)
            check(owner.store.authenticate(saved["credential"]) is None, "revoked integration denied")
            after_revoke = case / "after-revoke"
            backup(data, after_revoke)
            owner = start(after_revoke)
            check(owner.store.authenticate(saved["credential"]) is None, "tombstones survive restore")
            revived = case / "old-restore"
            backup(pre_revoke, revived)
            owner = start(revived)
            check(owner.store.authenticate(saved["credential"]) is not None, "old snapshot rollback risk")

            restored = case / "pre-upgrade-restore"
            backup(before, restored)
            check(snapshot(restored) == before_bytes, "pre-upgrade exact restore")
            CredentialStore(restored / "authz.json")  # Current reader validates the old fixture.
            check(snapshot(before) == before_bytes, "immutable original backup")
            for name, contents in original_settings.items():
                check((data / name).read_bytes() == contents, "settings preservation")
            print(f"PASS: {'no authz' if version == 0 else f'schema {version}'} -> schema 5; "
                  "owner transitions, private backup/restore, settings and revocation")
    print("PASS: all disposable data removed; no listeners or live services used")


if __name__ == "__main__":
    if len(sys.argv) != 1:
        sys.exit("No arguments accepted; this rehearsal never accepts an existing DATA_DIR")
    if sys.version_info < (3, 12):  # noqa: UP036 - source script can bypass package requires-python.
        sys.exit("Requires Python 3.12 or later with the core project dependencies installed")
    if not sys.flags.isolated:
        # Do not inherit auth, provider, proxy, PYTHONPATH, TMPDIR or home settings.
        result = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve())],
                                env={"PATH": os.defpath, "LANG": "C.UTF-8"}, cwd="/tmp", check=False)
        sys.exit(result.returncode)
    try:
        rehearse()
    except Exception as error:  # noqa: BLE001 - redact secrets from all failure messages.
        # Locations only: exception messages/locals may contain fixture secrets.
        trace = error.__traceback__
        while trace is not None:
            print(f"FAIL location: {Path(trace.tb_frame.f_code.co_filename).name}:{trace.tb_lineno}",
                  file=sys.stderr)
            trace = trace.tb_next
        sys.exit("FAIL: disposable migration rehearsal; inspect locally without logging credential values")
