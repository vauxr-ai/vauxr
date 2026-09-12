"""No Docker access: discovery mock and isolated helper filesystem tests."""
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

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


def helper(tmp_path, bind=False, injected='', backup_existing=False):
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
            'exclude': ['recordings'], 'backup_existing': backup_existing}
    return root, sources, lambda: subprocess.run([sys.executable, '-c', code, json.dumps(plan)],
                                                capture_output=True, text=True, check=False)


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
    assert plan == {'destination': str(tmp_path / 'data'), 'backup_existing': False,
                    'sources': [], 'consumers': [], 'exclude': []}
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
    assert len(checks) == 2 and all(check[3] is True for check in checks)
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
        + (['--plain'] if plain else []), capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ''
    assert result.stderr == (
        'Migration refused/failed: Root must be an existing canonical directory without symlinks or commas.. '
        'Sources and recovery copies are retained.\n'
    )


@pytest.mark.parametrize('stderr,expected', [
    ('Error: No such container: piper', 'does not exist'),
    ('permission denied while connecting', 'socket access denied'),
    ('Cannot connect to the Docker daemon', 'Cannot connect'),
    ('template parsing error: map has no entry', 'inspection template'),
    ('unexpected private diagnostic', 'unrecognized error'),
])
def test_command_failure_reports_operation_safely(monkeypatch, stderr, expected):
    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, '', stderr)
    monkeypatch.setattr(m.subprocess, 'run', run)
    with pytest.raises(m.MigrationError) as error:
        m.command(['docker', '--host', 'unix:///private/socket', 'container',
                   'inspect', '--format', 'private-template', 'piper'])
    message = str(error.value)
    assert "docker container inspect for 'piper'" in message
    assert expected in message
    assert 'private' not in message.replace('private data', '')
    assert 'docker --host' not in message


def test_inspection_tolerates_optional_host_config_fields(monkeypatch, tmp_path):
    _, calls = inventory(monkeypatch, tmp_path)
    m.discover([], ['vauxr', 'piper', 'whisper'], tmp_path, False)
    template = next(c[c.index('--format') + 1] for c in calls if c[:2] == ['container', 'inspect'])
    assert 'index .HostConfig "Mounts"' in template
    assert 'index .HostConfig "UsernsMode"' in template


def recreated_inventory(monkeypatch, tmp_path, state='exited', extra=None):
    containers, calls = inventory(monkeypatch, tmp_path, state=state, extra=extra)
    for container, role in zip(containers, ('vauxr', 'piper', 'whisper')):
        path = tmp_path / 'data' if role == 'vauxr' else tmp_path / 'data' / role
        container['Mounts'] = [{'Type': 'bind', 'Source': str(path), 'Destination': '/data'}]
    containers[0]['Mounts'] += [
        {'Type': 'bind', 'Source': str(tmp_path / name), 'Destination': '/data/' + name}
        for name in ('firmware', 'recordings')
    ]
    return containers, calls


@pytest.mark.parametrize('prefix', ['vauxr', 'vauxr-local'])
def test_recreated_containers_explicit_sources_do_not_require_originals(monkeypatch, tmp_path, prefix):
    _, calls = recreated_inventory(monkeypatch, tmp_path)
    selected = [f'{prefix}_{role}-data' for role in ('vauxr', 'piper', 'whisper')]
    plan = m.discover([], ['vauxr', 'piper', 'whisper'], tmp_path, True, selected)
    assert [s['source'] for s in plan['sources']] == selected
    assert plan['layout_status'] == 'already-bound'
    assert plan['migration_status'].startswith('unverified:')
    assert plan['source_selection_required'] == []
    assert plan['exclude'] == ['firmware', 'recordings']
    assert {c[-1] for c in calls if c[:2] == ['container', 'inspect']} == {'vauxr', 'piper', 'whisper'}


def test_already_bound_is_not_migration_complete_or_automatic_source_selection(monkeypatch, tmp_path):
    _, calls = recreated_inventory(monkeypatch, tmp_path, state='running')
    plan = m.discover([], ['vauxr', 'piper', 'whisper'], tmp_path, False)
    assert plan['layout_status'] == 'already-bound'
    assert plan['source_selection_required'] == ['piper', 'whisper']
    assert not any(c[:2] == ['volume', 'inspect'] for c in calls)
    with pytest.raises(m.MigrationError, match='--source-piper NAME --source-whisper NAME'):
        m.discover([], ['vauxr', 'piper', 'whisper'], tmp_path, True)


@pytest.mark.parametrize('mount', [
    {'Type': 'volume', 'Name': 'vauxr-local_piper-data'},
    {'Type': 'bind', 'Source': '/vol/vauxr-local_piper-data/sub'},
    {'Type': 'bind', 'Source': 'DESTINATION'},
])
def test_recovery_checks_all_source_and_destination_consumers(monkeypatch, tmp_path, mount):
    mount = dict(mount)
    if mount.get('Source') == 'DESTINATION':
        mount['Source'] = str(tmp_path / 'data' / 'whisper')
    recreated_inventory(monkeypatch, tmp_path,
                        extra={'Id': 'other', 'State': 'running', 'Mounts': [mount]})
    with pytest.raises(m.MigrationError, match='all consumers'):
        m.discover([], ['vauxr', 'piper', 'whisper'], tmp_path, True,
                   [f'vauxr-local_{role}-data' for role in ('vauxr', 'piper', 'whisper')])


def test_duplicate_explicit_sources_refused(monkeypatch, tmp_path):
    recreated_inventory(monkeypatch, tmp_path)
    with pytest.raises(m.MigrationError, match='distinct'):
        m.discover([], ['vauxr', 'piper', 'whisper'], tmp_path, True, ['same'] * 3)


@pytest.mark.parametrize('source', ['/tmp/source', 'volume,readonly', '../volume'])
def test_invalid_explicit_volume_never_reaches_docker(monkeypatch, source):
    monkeypatch.setattr(m, 'command', lambda args: pytest.fail('Invalid source must not reach Docker'))
    with pytest.raises(m.MigrationError, match='exact named volume'):
        m.volume_source([], source)


@pytest.mark.parametrize('bind', [False, True])
@pytest.mark.parametrize('failure', ['', 'copy', 'publish'])
def test_populated_recovery_preserves_whole_destination_and_rolls_back(tmp_path, bind, failure):
    injected = "        raise OSError('publication failure')\n" if failure == 'publish' else ''
    root, sources, run = helper(tmp_path, bind, injected, backup_existing=True)
    if failure == 'copy':
        os.mkfifo(sources[2] / 'unsupported')
    data = root / 'data'
    data.mkdir(exist_ok=True)
    (data / 'vauxr-identity.json').write_text('existing identity')
    os.utime(data / 'vauxr-identity.json', (1775952000, 1775952000))
    for role in ('piper', 'whisper', 'firmware'):
        (data / role).mkdir()
        (data / role / 'existing').write_text('existing ' + role)
    if not bind:
        (data / 'recordings').mkdir()
        (data / 'recordings' / 'existing').write_text('hidden recording')
    (data / 'piper' / 'link').symlink_to('existing')
    before = m.file_listing(data)
    result = run()
    assert (result.returncode != 0) is bool(failure), result.stderr
    retained = data if failure else root / '.data-backup-test'
    assert m.file_listing(retained) == before
    assert (retained / 'vauxr-identity.json').read_text() == 'existing identity'
    assert (retained / 'piper' / 'link').is_symlink()
    payload = root / '.data-migration-test' / 'payload' if failure else data
    assert (payload / 'piper' / 'file1').read_text() == 'content1'
    assert not (payload / 'piper' / 'existing').exists()
    assert (payload / 'vauxr-identity.json').exists() is bind
    assert (sources[2] / 'file2').read_text() == 'content2'


def test_backup_flag_still_refuses_symlink_destination(tmp_path):
    root, sources, run = helper(tmp_path, backup_existing=True)
    (root / 'data').symlink_to(sources[0], target_is_directory=True)
    assert run().returncode != 0
    assert not (root / '.data-backup-test').exists()


def test_helper_lock_prevents_second_apply(tmp_path):
    import fcntl
    root, _, run = helper(tmp_path, backup_existing=True)
    with open(root / '.data-migration.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run().returncode != 0
        assert not (root / '.data-migration-test').exists()


def test_candidate_inspection_both_sets_no_containers_no_contents(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('DOCKER_HOST', 'unix:///mock.sock')
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    monkeypatch.setattr(sys, 'argv', ['migrate-data.py', '--root', str(tmp_path), '--inspect-candidates'])
    names = [f'{prefix}_{role}-data' for prefix in ('vauxr', 'vauxr-local')
             for role in ('vauxr', 'piper', 'whisper')]
    for name in names:
        path = tmp_path / name
        path.mkdir()
        (path / 'vauxr-identity.json').write_text('PRIVATE KEY NEVER PRINT')
        (path / 'link').symlink_to('/unrelated/private/path')
    calls = []

    def command(args):
        calls.append(args)
        if args[3:5] == ['volume', 'ls']:
            return '\n'.join(names + ['unrelated'])
        assert args[3:5] == ['volume', 'inspect']
        return json.dumps([{'Driver': 'local', 'Options': None, 'Mountpoint': str(tmp_path / args[-1])}])

    monkeypatch.setattr(m, 'command', command)
    assert m.main() == 0
    out, err = capsys.readouterr()
    report = json.loads(out)
    assert {c['volume'] for c in report['candidates']} == set(names)
    assert all(c['files']['entries'][1]['name'] == 'vauxr-identity.json' for c in report['candidates'])
    assert 'PRIVATE KEY' not in out and '/unrelated/private/path' not in out
    assert 'no source selected' in err and 'SUCCESS' not in err
    assert len(calls) == 7
    assert not list(tmp_path.glob('.data-*'))


def test_listing_is_bounded_and_does_not_follow_symlinks(tmp_path):
    (tmp_path / 'file').write_text('secret')
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'sub' / 'back').symlink_to(tmp_path, target_is_directory=True)
    assert m.file_listing(tmp_path, limit=1)['status'] == 'truncated'
    assert len(m.file_listing(tmp_path)['entries']) == 3
    assert m.file_listing(tmp_path / 'sub' / 'back')['status'].startswith('unavailable')
    assert m.file_listing(tmp_path / 'missing')['status'].startswith('unavailable')


def test_explicit_cli_apply_forwards_selection_and_backup(monkeypatch, tmp_path):
    monkeypatch.setenv('DOCKER_HOST', 'unix:///mock.sock')
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    selected = [f'vauxr-local_{role}-data' for role in ('vauxr', 'piper', 'whisper')]
    argv = ['migrate-data.py', '--root', str(tmp_path), '--apply', '--backup-existing']
    for role, name in zip(('vauxr', 'piper', 'whisper'), selected):
        argv += ['--source-' + role, name]
    monkeypatch.setattr(sys, 'argv', argv)
    checks = []

    def discover(docker, names, root, apply, explicit):
        assert apply and explicit == selected
        checks.append(explicit)
        return {'sources': [{'type': 'volume', 'source': s} for s in explicit], 'exclude': [],
                'consumers': [], 'layout_status': 'already-bound'}

    def command(args):
        if args[3:5] == ['image', 'inspect']:
            return 'sha256:trusted'
        assert args[3] == 'run'
        assert json.loads(args[-1])['backup_existing'] is True
        for i, name in enumerate(selected):
            assert f'type=volume,source={name},target=/source{i},readonly,volume-nocopy' in args
        return 'mock publication'

    monkeypatch.setattr(m, 'discover', discover)
    monkeypatch.setattr(m, 'command', command)
    assert m.main() == 0
    assert len(checks) == 2


@pytest.mark.parametrize('role', ['piper', 'whisper'])
def test_populated_cache_requires_backup_flag(tmp_path, role):
    root, _, run = helper(tmp_path, bind=True)
    (root / 'data' / role).mkdir()
    (root / 'data' / role / 'existing').write_text('retain')
    assert run().returncode != 0
    assert (root / 'data' / role / 'existing').read_text() == 'retain'
    assert not (root / '.data-backup-test').exists()


@pytest.mark.parametrize('role', ['piper', 'whisper'])
def test_backup_flag_does_not_bypass_source_cache_collision(tmp_path, role):
    root, sources, run = helper(tmp_path, backup_existing=True)
    (sources[0] / role).mkdir()
    (root / 'data').mkdir()
    (root / 'data' / 'original').write_text('keep')
    assert run().returncode != 0
    assert (root / 'data' / 'original').read_text() == 'keep'


def test_inspection_cannot_apply(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'argv', ['migrate-data.py', '--root', str(tmp_path),
                                    '--inspect-candidates', '--apply'])
    monkeypatch.setattr(m, 'command', lambda args: pytest.fail('Must fail before Docker'))
    with pytest.raises(SystemExit) as error:
        m.main()
    assert error.value.code == 2
    assert not list(tmp_path.iterdir())


def test_listing_marks_inaccessible_subdirectories(monkeypatch, tmp_path):
    def fwalk(*args, **kwargs):
        kwargs['onerror'](PermissionError('private diagnostic'))
    monkeypatch.setattr(m.os, 'fwalk', fwalk)
    result = m.file_listing(tmp_path)
    assert result['status'].startswith('unavailable')
    assert 'private diagnostic' not in json.dumps(result)
