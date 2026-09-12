"""No Docker access: discovery mock and isolated helper filesystem tests."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SPEC = importlib.util.spec_from_file_location('migration', Path(__file__).parents[1] / 'scripts/migrate-data.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def inventory(monkeypatch, tmp_path, bind=False, state='exited', extra=None):
    mounts = []
    for i, name in enumerate(('vauxr', 'piper', 'whisper')):
        mount = {'Type': 'volume', 'Name': f'unpredictable-{i}', 'Destination': '/data'}
        if bind and i == 0:
            mount = {'Type': 'bind', 'Source': str(tmp_path / 'data'), 'Destination': '/data'}
        mounts.append({'Id': name, 'State': state, 'Mounts': [mount]})
    if extra:
        mounts.append(extra)
    calls = []

    def command(args):
        calls.append(args)
        if args[0:2] == ['container', 'ls']:
            return ' '.join(c['Id'] for c in mounts)
        if args[0:2] == ['container', 'inspect']:
            return json.dumps(next(c for c in mounts if c['Id'] == args[-1]))
        if args[0:2] == ['volume', 'inspect']:
            return json.dumps([{'Driver': 'local', 'Options': None, 'Mountpoint': '/vol/' + args[-1]}])
        raise AssertionError(args)

    monkeypatch.setattr(m, 'command', command)
    return mounts, calls


@pytest.mark.parametrize('bind', [False, True])
def test_actual_names_and_read_only_discovery(monkeypatch, tmp_path, bind):
    _, calls = inventory(monkeypatch, tmp_path, bind, 'running')
    plan = m.discover([], ['vauxr', 'piper', 'whisper'], tmp_path, False)
    assert plan['sources'][1]['source'] == 'unpredictable-1'
    assert plan['sources'][0]['type'] == ('bind' if bind else 'volume')
    assert all(c[1] in ('inspect', 'ls') for c in calls)


@pytest.mark.parametrize('kind', ['volume', 'bind'])
def test_checks_other_consumers(monkeypatch, tmp_path, kind):
    mount = {'Type': kind, 'Name': 'unpredictable-1', 'Source': '/vol/unpredictable-1/sub'}
    inventory(monkeypatch, tmp_path, extra={'Id': 'unrelated', 'State': 'running', 'Mounts': [mount]})
    with pytest.raises(m.MigrationError, match='all consumers'):
        m.discover([], ['vauxr', 'piper', 'whisper'], tmp_path, True)


def test_nested_mount_validation(monkeypatch, tmp_path):
    containers, _ = inventory(monkeypatch, tmp_path)
    child = {'Type': 'bind', 'Destination': '/data/recordings', 'Source': str(tmp_path / 'recordings')}
    containers[0]['Mounts'].append(child)
    assert m.discover([], ['vauxr', 'piper', 'whisper'], tmp_path, True)['exclude'] == ['recordings']
    child['Destination'] = '/data/unknown'
    with pytest.raises(m.MigrationError, match='nested'):
        m.discover([], ['vauxr', 'piper', 'whisper'], tmp_path, True)


def helper(tmp_path, bind=False, injected=''):
    root = tmp_path / 'repo'
    root.mkdir(exist_ok=True)
    sources = []
    for i in range(3):
        source = root / 'data' if bind and i == 0 else tmp_path / f'source{i}'
        source.mkdir(exist_ok=True)
        (source / f'file{i}').write_text(f'content{i}')
        os.chmod(source / f'file{i}', 0o640)
        sources.append(source)
    (sources[0] / 'recordings').mkdir(exist_ok=True)
    (sources[0] / 'recordings' / 'excluded').write_text('do not copy')
    (sources[1] / 'link').symlink_to('file1')
    (root / 'probe').write_text('test')
    code = m.HELPER.replace("pathlib.Path('/target')", f'pathlib.Path({str(root)!r})')
    code = code.replace("pathlib.Path('/source0')", f'pathlib.Path({str(sources[0])!r})')
    code = code.replace("pathlib.Path('/source' + str(i))", f'pathlib.Path({str(tmp_path)!r}) / ("source" + str(i))')
    code = code.replace("    try:\n        rename(stage / 'payload', data)",
                        "    try:\n" + injected + "        rename(stage / 'payload', data)")
    plan = {'probe': 'probe', 'nonce': 'test', 'sources': [{'type': 'bind' if bind else 'volume'}],
            'exclude': ['recordings']}
    return root, sources, lambda: subprocess.run([sys.executable, '-c', code, json.dumps(plan)],
                                                capture_output=True, text=True)


@pytest.mark.parametrize('bind', [False, True])
def test_publication_metadata_sources_and_backups(tmp_path, bind):
    root, sources, run = helper(tmp_path, bind)
    result = run()
    assert result.returncode == 0, result.stderr
    data = root / 'data'
    assert (data / 'file0').read_text() == 'content0'
    assert (data / 'piper' / 'file1').stat().st_mode & 0o777 == 0o640
    assert (data / 'piper' / 'file1').stat().st_uid == sources[1].stat().st_uid
    assert (data / 'piper' / 'link').is_symlink()
    assert not (data / 'recordings').exists()
    assert (sources[1] / 'file1').read_text() == 'content1'
    if bind:
        assert (root / '.data-backup-test' / 'recordings' / 'excluded').exists()
    else:
        assert (sources[0] / 'recordings' / 'excluded').exists()


def test_nonempty_destination_never_overwritten(tmp_path):
    root, _, run = helper(tmp_path)
    (root / 'data').mkdir()
    (root / 'data' / 'original').write_text('keep')
    assert run().returncode != 0
    assert (root / 'data' / 'original').read_text() == 'keep'
    assert not (root / '.data-migration-test').exists()


def test_failure_restores_original_retains_staging(tmp_path):
    root, _, run = helper(tmp_path, True, "        raise OSError('injected publication failure')\n")
    assert run().returncode != 0
    assert (root / 'data' / 'file0').read_text() == 'content0'
    assert (root / '.data-migration-test' / 'payload' / 'piper' / 'file1').exists()


def test_existing_cache_refused(tmp_path):
    root, _, run = helper(tmp_path, True)
    (root / 'data' / 'piper').symlink_to(tmp_path / 'source1')
    assert run().returncode != 0
    assert (root / 'data' / 'piper').is_symlink()


def test_path_namespace_mismatch_refused(tmp_path):
    root, _, run = helper(tmp_path)
    (root / 'probe').write_text('different filesystem')
    assert run().returncode != 0
    assert not (root / 'data').exists()


def test_copy_failure_keeps_destination_and_old_backups(tmp_path):
    root, sources, run = helper(tmp_path, True)
    old = root / '.data-backup-previous'
    old.mkdir()
    (old / 'keep').write_text('original backup')
    os.mkfifo(sources[2] / 'unsupported')
    assert run().returncode != 0
    assert (root / 'data' / 'file0').exists()
    assert (old / 'keep').read_text() == 'original backup'
    assert (root / '.data-migration-test' / 'payload' / 'piper' / 'file1').exists()


def test_publication_race_never_replaces_new_destination(tmp_path):
    root, _, run = helper(tmp_path, True, "        data.mkdir()\n        (data / 'new').write_text('keep new')\n")
    assert run().returncode != 0
    assert (root / 'data' / 'new').read_text() == 'keep new'
    assert (root / '.data-backup-test' / 'file0').exists()
    assert (root / '.data-migration-test' / 'payload' / 'file0').exists()


def test_empty_legacy_destination_is_backed_up(tmp_path):
    root, _, run = helper(tmp_path)
    (root / 'data').mkdir(mode=0o750)
    result = run()
    assert result.returncode == 0, result.stderr
    assert (root / '.data-backup-test').stat().st_mode & 0o777 == 0o750


def test_symlink_destination_refused(tmp_path):
    root, sources, run = helper(tmp_path)
    (root / 'data').symlink_to(sources[0], target_is_directory=True)
    assert run().returncode != 0
    assert (root / 'data').is_symlink()


@pytest.mark.parametrize('state', ['running', 'paused', 'restarting', 'dead', 'removing'])
def test_apply_requires_stopped_selected_services(monkeypatch, tmp_path, state):
    inventory(monkeypatch, tmp_path, state=state)
    with pytest.raises(m.MigrationError, match='all consumers'):
        m.discover([], ['vauxr', 'piper', 'whisper'], tmp_path, True)


def test_cli_remote_daemon_refused_without_writes(monkeypatch, tmp_path):
    monkeypatch.setenv('DOCKER_HOST', 'ssh://example')
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    monkeypatch.setattr(sys, 'argv', ['migrate-data.py', '--root', str(tmp_path), '--apply'])
    monkeypatch.setattr(m, 'command', lambda args: pytest.fail('Must not call remote daemon'))
    with pytest.raises(m.MigrationError, match='local Unix'):
        m.main()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('plain', [False, True])
def test_cli_default_dry_run_never_runs_helper(monkeypatch, tmp_path, capsys, plain):
    monkeypatch.setenv('DOCKER_HOST', 'unix:///mock.sock')
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    monkeypatch.setattr(sys, 'argv', ['migrate-data.py', '--root', str(tmp_path)]
                        + (['--plain'] if plain else []))
    monkeypatch.setattr(m, 'discover', lambda *args: {'sources': [], 'consumers': [], 'exclude': []})
    monkeypatch.setattr(m, 'command', lambda args: pytest.fail('Dry-run must not run helper'))
    assert m.main() == 0
    out, err = capsys.readouterr()
    plan, end = json.JSONDecoder().raw_decode(out)
    assert plan == {'destination': str(tmp_path / 'data'), 'sources': [], 'consumers': [], 'exclude': []}
    assert out[end:] == ('\nDRY RUN: no writes. Apply requires stopped consumers and validates '
                         'destination in the daemon.\n')
    assert err.startswith('PLAN: ')
    assert 'WARNING: Destination validation runs only with --apply' in err
    assert 'SUCCESS' not in err and 'VERIFIED' not in err
    assert err.isascii()
    assert not list(tmp_path.iterdir())


def test_advanced_volume_mount_refused(monkeypatch, tmp_path):
    containers, _ = inventory(monkeypatch, tmp_path)
    containers[0]['AdvancedMounts'] = [{'VolumeOptions': {'Subpath': 'subset'}}]
    with pytest.raises(m.MigrationError, match='subpaths'):
        m.discover([], ['vauxr', 'piper', 'whisper'], tmp_path, True)


@pytest.mark.parametrize('failure', [None, 'inventory', 'helper'])
def test_apply_helper_uses_readonly_actual_volumes_and_daemon_uid(monkeypatch, tmp_path, capsys, failure):
    monkeypatch.setenv('DOCKER_HOST', 'unix:///mock.sock')
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    monkeypatch.setattr(sys, 'argv', ['migrate-data.py', '--root', str(tmp_path), '--apply'])
    plan = {'sources': [{'type': 'volume', 'source': f'actual-{i}'} for i in range(3)],
            'consumers': [], 'exclude': []}
    checks = []
    calls = []

    def discover(*args):
        checks.append(args)
        if failure == 'inventory' and len(checks) == 2:
            return {**plan, 'exclude': ['recordings']}
        return plan

    def command(args):
        calls.append(args)
        if args[3:5] == ['image', 'inspect']:
            return 'sha256:trusted'
        assert args[3] == 'run'
        assert args[args.index('--user') + 1] == '0:0'
        assert '--pull=never' in args and '--network=none' in args and '--read-only' in args
        assert 'sha256:trusted' in args and '-I' in args
        for i in range(3):
            assert f'type=volume,source=actual-{i},target=/source{i},readonly,volume-nocopy' in args
        payload = json.loads(args[-1])
        assert (tmp_path / payload['probe']).read_text() == payload['nonce']
        out, err = capsys.readouterr()
        assert 'VERIFIED: Mounts and stopped consumers rechecked; inventory unchanged.' in err
        assert 'COPYING: Starting helper validation, staged copy and publication' in err
        assert 'Recovery paths: .data-migration-' in out and '\x1b' not in out
        assert 'SUCCESS' not in err
        if failure == 'helper':
            raise m.MigrationError('Command failed: docker --host (exit 1)')
        return 'mock copy completed'

    monkeypatch.setattr(m, 'discover', discover)
    monkeypatch.setattr(m, 'command', command)
    if failure:
        with pytest.raises(m.MigrationError):
            m.main()
    else:
        assert m.main() == 0
    out, err = capsys.readouterr()
    if failure:
        assert 'SUCCESS' not in err
        if failure == 'inventory':
            assert 'VERIFIED' not in err and 'COPYING' not in err
    else:
        assert 'SUCCESS: Published ./data; sources and recovery copies retained.' in err
        assert 'WARNING: Recreate services and verify settings, caches and voice' in err
        assert out == 'mock copy completed\n'
    assert len(checks) == 2 and all(check[-1] is True for check in checks)
    assert [call[3] for call in calls] == (['image'] if failure == 'inventory' else ['image', 'run'])
    assert not list(tmp_path.iterdir())


class Terminal(io.StringIO):
    def __init__(self, tty=True, encoding='utf-8'):
        super().__init__()
        self.tty = tty
        self._encoding = encoding

    @property
    def encoding(self):
        return self._encoding

    def isatty(self):
        return self.tty


@pytest.mark.parametrize('stdout_tty,stderr_tty,plain,no_color,term,encoding,decorated', [
    (True, True, False, None, 'xterm', 'utf-8', True),
    (False, True, False, None, 'xterm', 'utf-8', False),
    (True, False, False, None, 'xterm', 'utf-8', False),
    (False, False, False, None, 'xterm', 'utf-8', False),
    (True, True, True, None, 'xterm', 'utf-8', False),
    (True, True, False, '', 'xterm', 'utf-8', False),
    (True, True, False, '1', 'xterm', 'utf-8', False),
    (True, True, False, None, 'dumb', 'utf-8', False),
    (True, True, False, None, 'xterm', 'ascii', False),
])
def test_status_output_modes(monkeypatch, stdout_tty, stderr_tty, plain, no_color, term,
                             encoding, decorated):
    stdout, stderr = Terminal(stdout_tty), Terminal(stderr_tty, encoding)
    monkeypatch.setattr(sys, 'stdout', stdout)
    monkeypatch.setattr(sys, 'stderr', stderr)
    monkeypatch.setenv('TERM', term)
    monkeypatch.delenv('NO_COLOR', raising=False)
    if no_color is not None:
        monkeypatch.setenv('NO_COLOR', no_color)
    status = m.Status(plain=plain)
    for kind, message in [('plan', 'Sources discovered'), ('copying', 'Starting helper'),
                          ('verified', 'Inventory unchanged'), ('success', 'Published ./data'),
                          ('warning', 'Verify services')]:
        status.show(kind, message)
    output = stderr.getvalue()
    assert stdout.getvalue() == ''
    assert ('\x1b[' in output) is decorated
    assert ('📋' in output) is decorated
    for label in ('PLAN', 'COPYING', 'VERIFIED', 'SUCCESS', 'WARNING'):
        assert label in output
    assert 'Published ./data' in output
    if not decorated:
        assert output.isascii()


@pytest.mark.parametrize('plain', [False, True])
def test_cli_error_remains_plain_and_actionable(tmp_path, plain):
    # Invalid root fails before any Docker invocation.
    result = subprocess.run(
        [sys.executable, str(SPEC.origin), '--root', str(tmp_path / 'missing')]
        + (['--plain'] if plain else []), capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert result.stdout == ''
    assert result.stderr == (
        'Migration refused/failed: Root must be an existing canonical directory without symlinks or commas.. '
        'Sources and recovery copies are retained.\n'
    )
