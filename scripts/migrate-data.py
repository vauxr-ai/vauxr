#!/usr/bin/env python3
"""Offline, same-daemon migration. No Compose mutations or source deletion."""
import argparse
import concurrent.futures
import json
import os
import re
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar


class MigrationError(RuntimeError):
    pass


class ImageMissing(MigrationError):
    """Docker explicitly reports the requested image absent on the selected daemon."""


class Status:
    """Human progress on stderr; JSON, helper output and errors stay undecorated."""

    STYLES: ClassVar[dict[str, tuple[str, str]]] = {
        'plan': ('📋', '36'),
        'copying': ('📦', '34'),
        'stopping': ('⏹', '33'),
        'verified': ('🔎', '32'),
        'success': ('✅', '32'),
        'warning': ('⚠️', '33'),
    }

    def __init__(self, plain: bool = False) -> None:
        self.decorated = (
            not plain and 'NO_COLOR' not in os.environ and os.environ.get('TERM') != 'dumb'
            and sys.stdout.isatty() and sys.stderr.isatty()
        )
        if self.decorated:
            try:
                ''.join(icon for icon, _ in self.STYLES.values()).encode(sys.stderr.encoding or 'ascii')
            except (UnicodeEncodeError, LookupError):
                self.decorated = False

    def show(self, kind: str, message: str) -> None:
        label = kind.upper()
        if self.decorated:
            icon, color = self.STYLES[kind]
            label = f'\033[{color}m{icon} {label}\033[0m'
        print(f'{label}: {message}', file=sys.stderr, flush=True)


def command(args: list[str]) -> str:
    env = os.environ.copy()
    if '--host' in args:
        for key in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
            env.pop(key, None)
    result = subprocess.run(args, text=True, capture_output=True, check=False, env=env)
    if result.returncode:
        # Identify the operation, not global flags; never dump helper arguments,
        # container output or arbitrary daemon text into diagnostics.
        operation = args[3:] if args[1:2] == ['--host'] else args[1:]
        label = ' '.join([args[0], *operation[:1 if operation[:1] == ['pull'] else 2]])
        if operation[:2] == ['image', 'inspect'] and (result.stderr or '').strip() in (
            f'Error: No such image: {operation[-1]}',
            f'Error response from daemon: No such image: {operation[-1]}',
        ):
            raise ImageMissing('Configured helper image is absent on the selected Docker daemon.')
        if operation[:2] in (['container', 'inspect'], ['volume', 'inspect']):
            label += f" for {operation[-1]!a}"
        error = (result.stderr or '').lower()
        if (operation[:2] in (['container', 'inspect'], ['volume', 'inspect'])
                and 'no such' in error and any(kind in error for kind in ('container', 'object', 'volume'))):
            detail = ('Selected container or volume does not exist on this daemon. '
                      'Check docker container ls -a and select existing containers with '
                      '--vauxr, --piper and --whisper; recreated containers are supported. '
                      'Check explicit --source-vauxr/--source-piper/--source-whisper volume names.')
        elif 'permission denied' in error:
            detail = 'Docker socket access denied; use the same account/context that manages this stack.'
        elif 'cannot connect' in error or 'connection refused' in error:
            detail = 'Cannot connect to the selected Docker daemon; check its socket and running state.'
        elif 'template' in error or 'map has no entry' in error:
            detail = 'Docker could not render the container inspection template; check Docker version compatibility.'
        else:
            detail = ('Docker returned an unrecognized error. Check the indicated operation directly; '
                      'raw output is withheld because it may contain private data.')
        raise MigrationError(f"Command failed: {label} (exit {result.returncode}). {detail}")
    return result.stdout


def helper_image(docker: list[str], reference: str, apply: bool, status: Status) -> str | None:
    inspect = docker + ['image', 'inspect', '--format', '{{.Id}}', reference]
    try:
        image = command(inspect).strip()
    except ImageMissing:
        if not apply:
            status.show('warning', 'Helper image is absent; --apply will pull it before any stops or writes. '
                        'Dry-run will not pull; helper availability remains unverified.')
            return None
        status.show('plan', 'Pulling missing helper image before any consumer stops or data writes.')
        try:
            command(docker + ['pull', reference])
            image = command(inspect).strip()
        except (MigrationError, OSError) as exc:
            raise MigrationError('Helper image preparation failed; no consumers were stopped and no migration '
                                 'data was written. Check the configured --helper-image, registry access and '
                                 'Docker connectivity, then retry. ' + str(exc)) from exc
    if not image:
        raise MigrationError('Helper image inspection returned no image ID; refusing migration.')
    status.show('plan', 'Helper image inspected; apply uses its local image ID without further pulls.')
    return image


def overlap(a: str, b: str) -> bool:
    return a == b or a.startswith(b.rstrip('/') + '/') or b.startswith(a.rstrip('/') + '/')


def volume_source(docker: list[str], name: str) -> dict:
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]+', name):
        raise MigrationError('Source must be an exact named volume, not a path or mount expression.')
    volume = json.loads(command(docker + ['volume', 'inspect', name]))[0]
    if volume['Driver'] != 'local' or volume.get('Options'):
        raise MigrationError('Only plain local named volumes are supported.')
    return {'type': 'volume', 'source': name, 'path': volume['Mountpoint'],
            'created_at': volume.get('CreatedAt')}


def file_listing(path: Path, limit: int = 200) -> dict:
    """Advisory host metadata only; never open files or follow directory symlinks."""
    entries = []

    def failed(error: OSError) -> None:
        raise error

    try:
        if path.resolve() != path or not path.is_absolute():
            return {'status': 'unavailable: noncanonical host path'}
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for base, dirs, files, directory_fd in os.fwalk(
                '.', dir_fd=fd, follow_symlinks=False, onerror=failed,
            ):
                dirs.sort()
                for name in sorted(dirs + files):
                    if len(entries) == limit:
                        return {'status': 'truncated', 'limit': limit, 'entries': entries}
                    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    kind = ('directory' if stat.S_ISDIR(info.st_mode) else
                            'file' if stat.S_ISREG(info.st_mode) else
                            'symlink' if stat.S_ISLNK(info.st_mode) else 'special')
                    entries.append({'name': str(Path(base) / name), 'type': kind, 'bytes': info.st_size,
                                    'mtime_utc': datetime.fromtimestamp(info.st_mtime, UTC).isoformat()})
        finally:
            os.close(fd)
    except OSError:
        return {'status': 'unavailable or changed: host permissions/path visibility; no elevation attempted',
                'entries': entries}
    return {'status': 'listed', 'entries': entries}


def inspect_candidates(docker: list[str], root: Path, requested: list[str]) -> dict:
    """Listing a candidate is not selecting it; prefixes are not provenance."""
    names = command(docker + ['volume', 'ls', '--format', '{{.Name}}']).splitlines()
    candidates = sorted(set(requested) | {
        name for name in names if re.search(r'(^|_)(vauxr|piper|whisper)-data$', name)
    })
    result = []
    for name in candidates:
        source = volume_source(docker, name)
        result.append({'volume': name, 'driver': 'local', 'created_at': source['created_at'],
                       'files': file_listing(Path(source['path']))})
    return {'candidates': result, 'destination_files': file_listing(root / 'data'),
            'notice': 'Host metadata only; daemon path visibility is unverified until apply. '
                      'Listings may change while writers run; dates/names do not prove provenance or equality. '
                      'No file contents, symlink targets, labels or credentials are read or printed.'}


def discover(docker: list[str], names: list[str], root: Path, apply: bool,
             explicit: list[str | None] | None = None, *, allow_running: bool = False,
             migration_helper: str | None = None) -> dict:
    explicit = explicit or [None, None, None]

    def inspect(name: str) -> dict:
        # Never request environment variables or other unrelated container configuration.
        fmt = ('{"Name":{{json .Name}},"Id":{{json .Id}},"State":{{json .State.Status}},"Mounts":{{json .Mounts}},'
               '"AdvancedMounts":{{json (index .HostConfig "Mounts")}},"Userns":{{json (index .HostConfig "UsernsMode")}}}')
        return json.loads(command(docker + ['container', 'inspect', '--format', fmt, name]))

    selected = [inspect(name) for name in names]
    if len({c['Id'] for c in selected}) != 3:
        raise MigrationError('Select three distinct existing containers.')
    sources = []
    separate = []
    layout = []
    required = []
    for index, container in enumerate(selected):
        if container.get('Userns') or any(
            (mount.get('VolumeOptions') or {}).get('Subpath')
            for mount in (container.get('AdvancedMounts') or [])
        ):
            raise MigrationError('Per-container user namespaces and volume subpaths are unsupported.')
        mounts = container['Mounts']
        data = [m for m in mounts if m['Destination'] == '/data']
        if len(data) != 1:
            raise MigrationError('Each selected container must have exactly one /data mount.')
        mount = data[0]
        nested = [m for m in mounts if m['Destination'].startswith('/data/')]
        for child in nested:
            if (index != 0 or child['Destination'] not in ('/data/recordings', '/data/firmware')
                    or child['Type'] != 'bind' or overlap(child['Source'], str(root / 'data'))):
                raise MigrationError('Unsupported nested mount; only separate recordings/firmware binds allowed.')
            separate.append(child['Destination'].split('/')[-1])
        role = ('vauxr', 'piper', 'whisper')[index]
        destination = root / 'data' if index == 0 else root / 'data' / role
        if mount['Type'] == 'bind' and mount['Source'] == str(destination):
            current = {'type': 'bind', 'source': mount['Source']}
            if index > 0 and not explicit[index]:
                required.append(role)
        elif mount['Type'] == 'volume' and mount.get('Name'):
            current = volume_source(docker, mount['Name'])
        else:
            raise MigrationError('Expected named /data volumes or the exact ./data, ./data/piper, ./data/whisper binds.')
        layout.append({'role': role, **current})
        sources.append(volume_source(docker, explicit[index]) if explicit[index] else current)
    if apply and required:
        raise MigrationError('Already-bound caches do not identify old sources. Select exact named volumes with '
                             + ' '.join('--source-' + role + ' NAME' for role in required)
                             + '; use --inspect-candidates first. Migration completeness is unknown.')
    volumes = [s['source'] for s in sources if s['type'] == 'volume']
    if len(set(volumes)) != len(volumes):
        raise MigrationError('Source volumes must be distinct.')
    paths = [str(root / 'data')] + [s['path'] for s in sources if s['type'] == 'volume']
    ids = command(docker + ['container', 'ls', '-aq', '--no-trunc']).split()
    consumers = []
    for cid in ids:
        c = inspect(cid)
        if migration_helper and c.get('Name') == '/' + migration_helper:
            continue  # Only our nonce-named helper may hold these mounts during the publication check.
        relevant = c['Id'] in {s['Id'] for s in selected} or any(
            (m['Type'] == 'volume' and m.get('Name') in volumes)
            or (m['Type'] == 'bind' and any(overlap(m['Source'], p) for p in paths))
            for m in c['Mounts']
        )
        if relevant:
            consumers.append({'id': c['Id'], 'name': c.get('Name', c['Id']).lstrip('/'), 'state': c['State']})
            if apply and c['State'] not in (('exited', 'created', 'running') if allow_running else ('exited', 'created')):
                raise MigrationError(f"Consumer {c.get('Name', c['Id'])} ({c['Id']}) is {c['State']}; "
                                     "all consumers must be stopped. Resolve this state manually; no force is used.")
    if not {s['Id'] for s in selected}.issubset({c['id'] for c in consumers}):
        raise MigrationError('Container inventory changed; retry discovery.')
    return {'sources': sources, 'exclude': separate, 'consumers': consumers,
            'current_layout': layout, 'source_selection_required': required,
            'layout_status': 'already-bound' if all(s['type'] == 'bind' for s in layout) else 'legacy-or-mixed',
            'migration_status': 'unverified: mount layout does not establish data provenance or completeness'}


# Executed inside the selected daemon's UID namespace, never on live service containers.
HELPER = r'''
import ctypes, fcntl, json, os, pathlib, shutil, stat, subprocess, sys, time
p = json.loads(sys.argv[1])
root = pathlib.Path('/target')
assert (root / p['probe']).read_text() == p['nonce'], 'Client/daemon path mismatch'
if not p.get('preflight'):
    lock = os.open(root / '.data-migration.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
libc = ctypes.CDLL(None, use_errno=True)
def rename(a, b):
    if libc.renameat2(-100, os.fsencode(a), -100, os.fsencode(b), 1):
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))
def directory(path):
    assert not path.is_symlink() and path.is_dir(), 'Expected real directory'
def empty(path):
    directory(path)
    assert not any(path.iterdir()), 'Destination contains data; explicit --backup-existing is required'
def validate_tree(path, excluded):
    for base, dirs, files in os.walk(path, followlinks=False):
        if pathlib.Path(base) == path:
            dirs[:] = [name for name in dirs if name not in excluded]
            files = [name for name in files if name not in excluded]
        for name in dirs + files:
            mode = os.lstat(os.path.join(base, name)).st_mode
            assert stat.S_ISDIR(mode) or stat.S_ISREG(mode) or stat.S_ISLNK(mode), 'Special file unsupported'
def copy(source, dest, excluded):
    directory(source)
    validate_tree(source, excluded)
    dest.mkdir(mode=0o700)
    for entry in source.iterdir():
        if entry.name not in excluded:
            subprocess.run(['cp', '-R', '-P', '--preserve=mode,ownership,timestamps,links,xattr',
                            '--', str(entry), str(dest)], check=True)
    metadata(source, dest)
def metadata(source, dest):
    info = source.stat()
    os.chown(dest, info.st_uid, info.st_gid)
    os.chmod(dest, stat.S_IMODE(info.st_mode))
    for key in os.listxattr(source):
        os.setxattr(dest, key, os.getxattr(source, key))
    os.utime(dest, ns=(info.st_atime_ns, info.st_mtime_ns))
data = root / 'data'
stage = root / ('.data-migration-' + p['nonce'])
backup = root / ('.data-backup-' + p['nonce'])
existing = os.path.lexists(data)
if existing:
    directory(data)
    assert not os.path.ismount(data), 'Destination must not be a mount point'
    if p.get('backup_existing'):
        pass  # The complete destination is renamed to backup, never merged or removed.
    elif p['sources'][0]['type'] == 'volume':
        empty(data)
    else:
        for name in ('piper', 'whisper'):
            if os.path.lexists(data / name):
                empty(data / name)
else:
    assert p['sources'][0]['type'] == 'volume', 'Existing bind disappeared'
if p.get('preflight'):
    assert shutil.which('cp'), 'Helper needs GNU coreutils cp'
    assert getattr(libc, 'renameat2', None), 'Helper needs renameat2 support'
    for i in range(3):
        source = pathlib.Path('/source' + str(i))
        directory(source)
        validate_tree(source, set(p['exclude']) | {'piper', 'whisper'} if i == 0 else set())
    if p['sources'][0]['type'] == 'volume':
        for name in ('piper', 'whisper'):
            assert not os.path.lexists(pathlib.Path('/source0') / name), 'Legacy data has cache name collision'
    sys.exit(0)
original = data.stat() if existing else None
assert not os.path.lexists(stage) and not os.path.lexists(backup)
# Verify kernel/filesystem support for no-replace renames before copying.
a = root / ('.data-rename-' + p['nonce'])
a.mkdir(mode=0o700)
rename(a, stage)
# Staging remains private while it is incomplete; payload inherits source metadata.
try:
    copy(pathlib.Path('/source0'), stage / 'payload', set(p['exclude']) | {'piper', 'whisper'})
    if p['sources'][0]['type'] == 'volume':
        for name in ('piper', 'whisper'):
            assert not os.path.lexists(pathlib.Path('/source0') / name), 'Legacy data has cache name collision'
    for i, name in enumerate(('piper', 'whisper'), 1):
        copy(pathlib.Path('/source' + str(i)), stage / 'payload' / name, set())
    metadata(pathlib.Path('/source0'), stage / 'payload')
    os.sync()
    if p.get('guard'):
        (root / ('.data-ready-' + p['nonce'])).touch(exist_ok=False)
        deadline = time.monotonic() + 300
        while not (root / ('.data-publish-' + p['nonce'])).exists():
            assert not (root / ('.data-abort-' + p['nonce'])).exists(), 'Consumer guard failed; publication aborted'
            assert time.monotonic() < deadline, 'Publication approval timed out; keep consumers stopped'
            time.sleep(0.1)
    if existing:
        current = data.stat()
        assert (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino), 'Destination changed'
        rename(data, backup)
    try:
        rename(stage / 'payload', data)
    except BaseException:
        if existing:
            rename(backup, data)
        raise
    os.sync()
    print('Published ./data; original destination retained at ' + str(backup.name) if existing
          else 'Published ./data; no previous destination existed')
finally:
    print('Recovery staging: ' + stage.name, flush=True)
'''


def consumer_label(consumer: dict) -> str:
    return f"{consumer.get('name', consumer['id'])} ({consumer['id']})"


def guarded_copy(args: list[str], root: Path, nonce: str, verify: Callable[[], None]) -> str:
    """Keep the helper's filesystem lock held while the client checks publication."""
    ready = root / ('.data-ready-' + nonce)
    publish = root / ('.data-publish-' + nonce)
    abort = root / ('.data-abort-' + nonce)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(command, args)
            try:
                while not future.done():
                    if ready.exists() and not publish.exists():
                        verify()
                        publish.touch(exist_ok=False)
                    time.sleep(0.05)
                return future.result()
            except BaseException:
                abort.touch(exist_ok=True)
                raise
    finally:
        for path in (ready, publish, abort):
            path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='validate, gracefully stop running consumers, copy and publish; default is read-only')
    parser.add_argument('--root', type=Path, default=Path.cwd(), help='repository directory on the Docker host')
    parser.add_argument('--vauxr', default='vauxr')
    parser.add_argument('--piper', default='piper')
    parser.add_argument('--whisper', default='whisper')
    for role in ('vauxr', 'piper', 'whisper'):
        parser.add_argument('--source-' + role, help='exact old named volume; overrides current container mount')
    parser.add_argument('--backup-existing', action='store_true',
                        help='allow populated destination; retain ALL existing ./data in an atomic rename backup')
    parser.add_argument('--inspect-candidates', action='store_true',
                        help='read-only candidate and destination filenames/sizes/mtimes; no source selection')
    parser.add_argument('--inspect-volume', action='append', default=[], metavar='NAME',
                        help='include another exact volume in candidate inspection (repeatable)')
    parser.add_argument('--helper-image', default='python:3.12-slim',
                        help='trusted compatible helper image; apply pulls only if absent locally')
    parser.add_argument('--plain', action='store_true', help='disable colors and emojis')
    args = parser.parse_args()
    if args.apply and (args.inspect_candidates or args.inspect_volume):
        parser.error('candidate inspection is read-only; run --apply separately')
    status = Status(args.plain)
    root = args.root.absolute()
    if root.resolve() != root or not root.is_dir() or ',' in str(root):
        raise MigrationError('Root must be an existing canonical directory without symlinks or commas.')
    if os.environ.get('DOCKER_CONTEXT') or not os.environ.get('DOCKER_HOST'):
        context = command(['docker', 'context', 'show']).strip()
        endpoint = json.loads(command(['docker', 'context', 'inspect', context]))[0]['Endpoints']['docker']['Host']
    else:
        endpoint = os.environ['DOCKER_HOST']
    if not endpoint.startswith('unix:///'):
        raise MigrationError('Only a local Unix-socket daemon is supported; run on the Docker host.')
    docker = ['docker', '--host', endpoint]
    explicit = [args.source_vauxr, args.source_piper, args.source_whisper]
    if args.inspect_candidates or args.inspect_volume:
        status.show('plan', 'Read-only candidate inspection; no source selected and no migration performed.')
        print(json.dumps(inspect_candidates(docker, root, args.inspect_volume + [s for s in explicit if s]),
                         indent=2), flush=True)
        return 0
    names = [args.vauxr, args.piper, args.whisper]
    plan = discover(docker, names, root, args.apply, explicit, allow_running=True)
    status.show('plan', 'Discovered sources and consumers; destination plan follows on stdout.')
    print(json.dumps({'destination': str(root / 'data'), 'backup_existing': args.backup_existing,
                      **plan}, indent=2), flush=True)
    if plan.get('layout_status') == 'already-bound':
        status.show('warning', 'Already bound to ./data and caches; this does not mean old data was migrated.')
    if not args.backup_existing:
        status.show('warning', 'Populated recovery targets require --backup-existing; no merge is performed.')
    for consumer in plan['consumers']:
        action = 'Will gracefully stop' if consumer['state'] == 'running' else 'No stop planned for'
        status.show('plan', f"{action} {consumer_label(consumer)}: {consumer['state']}")
    image = helper_image(docker, args.helper_image, args.apply, status)
    if not args.apply:
        print('DRY RUN: no writes. Apply automatically stops running consumers after preflight.')
        status.show('warning', 'Destination validation runs only with --apply; problematic states fail safely.')
        return 0
    nonce = uuid.uuid4().hex
    probe = root / ('.data-probe-' + nonce)
    with open(probe, 'x', opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
        f.write(nonce)
    stop_attempted = False
    try:
        mounts = ['--mount', f'type=bind,source={root},target=/target']
        for i, source in enumerate(plan['sources']):
            spec = f"type={source['type']},source={source['source']},target=/source{i},readonly"
            if source['type'] == 'volume':
                spec += ',volume-nocopy'
            mounts += ['--mount', spec]
        payload = {**plan, 'probe': probe.name, 'nonce': nonce, 'backup_existing': args.backup_existing}
        helper_args = docker + ['run', '--rm', '--pull=never', '--network=none', '--read-only',
                                '--user', '0:0', *mounts, '--entrypoint', 'python3', image, '-I', '-c', HELPER]
        # Read-only daemon-side checks must succeed before disrupting consumers.
        preflight_args = [arg.replace('target=/target', 'target=/target,readonly') for arg in helper_args]
        command(preflight_args + [json.dumps({**payload, 'preflight': True})])
        if discover(docker, names, root, True, explicit, allow_running=True) != plan:
            raise MigrationError('Mounts or consumers changed during preflight; retry.')
        expected = {**plan, 'consumers': [dict(c) for c in plan['consumers']]}
        for consumer in expected['consumers']:
            if consumer['state'] == 'running':
                stop_attempted = True
                status.show('stopping', f'Gracefully stopping {consumer_label(consumer)}.')
                # -1 disables Docker's SIGKILL timeout escalation. Never force shutdown.
                command(docker + ['stop', '--time=-1', consumer['id']])
                state = command(docker + ['container', 'inspect', '--format', '{{.State.Status}}',
                                          consumer['id']]).strip()
                if state != 'exited':
                    raise MigrationError(f"Consumer {consumer_label(consumer)} did not stop: {state!a}.")
                consumer['state'] = 'exited'

        helper_name = 'vauxr-data-migration-' + nonce

        def verify(*, copying: bool = False) -> None:
            if discover(docker, names, root, True, explicit,
                        migration_helper=helper_name if copying else None) != expected:
                raise MigrationError('Mounts or consumers changed; possible restart race. Keep writers stopped.')

        verify()
        status.show('verified', 'Mounts and stopped consumers rechecked; inventory unchanged.')
        print(f'Recovery paths: .data-migration-{nonce}, .data-backup-{nonce}', flush=True)
        status.show('copying', 'Starting helper validation, staged copy and publication; keep all writers stopped.')
        copy_args = helper_args[:4] + ['--name', helper_name] + helper_args[4:]
        print(guarded_copy(copy_args + [json.dumps({**payload, 'guard': True})],
                           root, nonce, lambda: verify(copying=True)))
        verify()
    except BaseException:
        if stop_attempted:
            status.show('warning', 'Stop was attempted; no consumers will be restarted. Keep all consumers stopped: '
                        + ', '.join(consumer_label(c) for c in plan['consumers']))
        print(f'Recovery: keep writers/restart automation disabled. Verify the migration helper has exited. '
              f'Inspect {root / "data"}, {root / (".data-backup-" + nonce)} and '
              f'{root / (".data-migration-" + nonce)}. Before publication, data is unchanged; '
              'if data is missing, restore the whole backup after preserving any partial data separately. '
              'Never publish incomplete staging. Resolve the error and rerun the same explicit source selections '
              'and --backup-existing when required. To resume after migration or rollback, recreate all consumers '
              'with the intended mounts (see current_layout for previous mounts); do not simply restart stale '
              'bind inodes. Verify settings, caches and voice before enabling automation.', file=sys.stderr)
        raise
    finally:
        probe.unlink()
    status.show('success', 'Published ./data; sources and recovery copies retained.')
    status.show('warning', 'Recreate services and verify settings, caches and voice before completing migration.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (MigrationError, OSError, ValueError, KeyError) as exc:
        print(f'Migration refused/failed: {exc}. Sources and recovery copies are retained.', file=sys.stderr)
        raise SystemExit(1)
