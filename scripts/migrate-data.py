#!/usr/bin/env python3
"""Offline, same-daemon migration. No Compose mutations or source deletion."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


class MigrationError(RuntimeError):
    pass


def command(args: list[str]) -> str:
    env = os.environ.copy()
    if '--host' in args:
        for key in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
            env.pop(key, None)
    result = subprocess.run(args, text=True, capture_output=True, check=False, env=env)
    if result.returncode:
        # Do not print arbitrary daemon/container output (it may contain private data).
        raise MigrationError(f"Command failed: {args[0]} {args[1]} (exit {result.returncode})")
    return result.stdout


def overlap(a: str, b: str) -> bool:
    return a == b or a.startswith(b.rstrip('/') + '/') or b.startswith(a.rstrip('/') + '/')


def discover(docker: list[str], names: list[str], root: Path, apply: bool) -> dict:
    def inspect(name: str) -> dict:
        # Never request environment variables or other unrelated container configuration.
        fmt = ('{"Id":{{json .Id}},"State":{{json .State.Status}},"Mounts":{{json .Mounts}},'
               '"AdvancedMounts":{{json .HostConfig.Mounts}},"Userns":{{json .HostConfig.UsernsMode}}}')
        return json.loads(command(docker + ['container', 'inspect', '--format', fmt, name]))

    selected = [inspect(name) for name in names]
    if len({c['Id'] for c in selected}) != 3:
        raise MigrationError('Select three distinct existing containers.')
    sources = []
    separate = []
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
        if mount['Type'] == 'bind' and index == 0 and mount['Source'] == str(root / 'data'):
            sources.append({'type': 'bind', 'source': mount['Source']})
        elif mount['Type'] == 'volume' and mount.get('Name'):
            volume = json.loads(command(docker + ['volume', 'inspect', mount['Name']]))[0]
            if volume['Driver'] != 'local' or volume.get('Options'):
                raise MigrationError('Only plain local named volumes are supported.')
            sources.append({'type': 'volume', 'source': mount['Name'], 'path': volume['Mountpoint']})
        else:
            raise MigrationError('Expected named caches and named /data or the exact existing ./data bind.')
    volumes = [s['source'] for s in sources if s['type'] == 'volume']
    if len(set(volumes)) != len(volumes):
        raise MigrationError('Source volumes must be distinct.')
    paths = [str(root / 'data')] + [s['path'] for s in sources if s['type'] == 'volume']
    ids = command(docker + ['container', 'ls', '-aq', '--no-trunc']).split()
    consumers = []
    for cid in ids:
        c = inspect(cid)
        relevant = c['Id'] in {s['Id'] for s in selected} or any(
            (m['Type'] == 'volume' and m.get('Name') in volumes)
            or (m['Type'] == 'bind' and any(overlap(m['Source'], p) for p in paths))
            for m in c['Mounts']
        )
        if relevant:
            consumers.append({'id': c['Id'], 'state': c['State']})
            if apply and c['State'] not in ('exited', 'created'):
                raise MigrationError(f"Consumer {c['Id']} is {c['State']}; all consumers must be stopped.")
    if not {s['Id'] for s in selected}.issubset({c['id'] for c in consumers}):
        raise MigrationError('Container inventory changed; retry discovery.')
    return {'sources': sources, 'exclude': separate, 'consumers': consumers}


# Executed inside the selected daemon's UID namespace, never on live service containers.
HELPER = r'''
import ctypes, fcntl, json, os, pathlib, stat, subprocess, sys
p = json.loads(sys.argv[1])
root = pathlib.Path('/target')
assert (root / p['probe']).read_text() == p['nonce'], 'Client/daemon path mismatch'
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
    assert not any(path.iterdir()), 'Destination contains data; move it aside manually'
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
    if p['sources'][0]['type'] == 'volume':
        empty(data)
    else:
        for name in ('piper', 'whisper'):
            if os.path.lexists(data / name):
                empty(data / name)
else:
    assert p['sources'][0]['type'] == 'volume', 'Existing bind disappeared'
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='copy and publish; default is read-only discovery')
    parser.add_argument('--root', type=Path, default=Path.cwd(), help='repository directory on the Docker host')
    parser.add_argument('--vauxr', default='vauxr')
    parser.add_argument('--piper', default='piper')
    parser.add_argument('--whisper', default='whisper')
    parser.add_argument('--helper-image', default='python:3.12-slim', help='already installed trusted helper image')
    args = parser.parse_args()
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
    names = [args.vauxr, args.piper, args.whisper]
    plan = discover(docker, names, root, args.apply)
    print(json.dumps({'destination': str(root / 'data'), **plan}, indent=2), flush=True)
    if not args.apply:
        print('DRY RUN: no writes. Apply requires stopped consumers and validates destination in the daemon.')
        return 0
    image = command(docker + ['image', 'inspect', '--format', '{{.Id}}', args.helper_image]).strip()
    nonce = uuid.uuid4().hex
    probe = root / ('.data-probe-' + nonce)
    with open(probe, 'x', opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
        f.write(nonce)
    try:
        if discover(docker, names, root, True) != plan:
            raise MigrationError('Mounts or consumers changed; retry.')
        mounts = ['--mount', f'type=bind,source={root},target=/target']
        for i, source in enumerate(plan['sources']):
            spec = f"type={source['type']},source={source['source']},target=/source{i},readonly"
            if source['type'] == 'volume':
                spec += ',volume-nocopy'
            mounts += ['--mount', spec]
        payload = {**plan, 'probe': probe.name, 'nonce': nonce}
        print(f'Recovery paths: .data-migration-{nonce}, .data-backup-{nonce}', flush=True)
        print(command(docker + ['run', '--rm', '--pull=never', '--network=none', '--read-only',
                               '--user', '0:0', *mounts, '--entrypoint', 'python3', image,
                               '-I', '-c', HELPER, json.dumps(payload)]))
    finally:
        probe.unlink()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (MigrationError, OSError, ValueError, KeyError) as exc:
        print(f'Migration refused/failed: {exc}. Sources and recovery copies are retained.', file=sys.stderr)
        raise SystemExit(1)
