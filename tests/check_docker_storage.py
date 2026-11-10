"""Disposable Docker integration check: python3 tests/check_docker_storage.py.

Build the storage-init target first. Uses only a unique Compose project and
unique /tmp bind paths on the selected daemon; never starts the real server.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import uuid


def run(*args, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=True)


def main():
    actual = json.loads(run('docker', 'compose', 'config', '--format', 'json').stdout)
    for service in ('vauxr', 'piper', 'whisper'):
        assert actual['services'][service]['depends_on']['storage-init']['condition'] == 'service_completed_successfully'
    assert actual['services']['storage-init']['build']['target'] == 'storage-init'
    for service in ('vauxr', 'storage-init'):
        assert {v['target'] for v in actual['services'][service]['volumes']} == {'/data', '/data/recordings', '/data/firmware'}
    project = 'vauxr-storage-test-'  + uuid.uuid4().hex[:12]
    root = '/tmp/' + project
    image = 'vauxr-setup-build-storage-init:latest'
    mounts = [f'{root}/data:/data', f'{root}/recordings:/data/recordings',
              f'{root}/firmware:/data/firmware']
    init = {'image': image, 'user': '0:0', 'network_mode': 'none', 'volumes': mounts}
    probe = {'image': image, 'user': '100:101', 'network_mode': 'none',
             'volumes': mounts, 'depends_on': {'storage-init': {
                 'condition': 'service_completed_successfully'}},
             'command': ['python', '-c', '''from pathlib import Path
for d in ('/data', '/data/recordings', '/data/firmware'):
 p=Path(d)/'probe'; p.write_text('persistent'); assert p.read_text()=='persistent'
''']}
    config = {'services': {'storage-init': init, 'probe': probe}}
    with tempfile.TemporaryDirectory(prefix=project) as tmp:
        file = Path(tmp) / 'compose.json'
        def compose(*args, check=True):
            return run('docker', 'compose', '-p', project, '-f', str(file), *args, check=check)
        def helper(code):
            return run('docker', 'run', '--rm', '--network', 'none', '-v', root+':/fixture',
                       image, 'python', '-c', code)
        try:
            file.write_text(json.dumps(config))
            compose('up', '-d')
            compose('wait', 'probe')
            print('PASS fresh mounts writable as UID 100:GID 101')
            helper('''from pathlib import Path
import os
for d in ('data/piper', 'data/whisper'):
 p=Path('/fixture')/d; p.mkdir(exist_ok=True)
 p.chmod(0o755); os.chown(p, 0, 0)
 p=p/'model'; p.write_text('cached'); p.chmod(0o640)
p=Path('/fixture/data/existing'); p.write_text('keep'); p.chmod(0o640)
''')
            compose('down')
            compose('up', '-d')
            compose('wait', 'probe')
            helper('''from pathlib import Path
for name in ('data/existing', 'data/piper/model', 'data/whisper/model'):
 p=Path('/fixture')/name
 assert p.read_text() == ('keep' if name.endswith('existing') else 'cached')
 assert p.stat().st_uid == 0 and p.stat().st_gid == 0
 assert p.stat().st_mode & 0o777 == 0o640
for name in ('data/piper', 'data/whisper'):
 assert (Path('/fixture')/name).stat().st_uid == 0
''')
            print('PASS repeat startup preserves existing files, ownership, modes and sibling caches')
            compose('down')
            init['volumes'] = [m+':ro' for m in mounts]
            file.write_text(json.dumps(config))
            result = compose('up', '-d', check=False)
            assert result.returncode != 0, result.stdout + result.stderr
            output = compose('ps', '-a', '--format', 'json').stdout.strip()
            state = json.loads(output) if output.startswith('[') else [json.loads(line) for line in output.splitlines()]
            assert all(s['State'] != 'running' for s in state if s['Service'] == 'probe')
            assert compose('logs', 'storage-init').stdout.find('Read-only file system') >= 0
            print('PASS failed initialization blocks dependent startup')
        finally:
            compose('down', '--remove-orphans', check=False)
            helper('''from pathlib import Path
import shutil
for p in Path('/fixture').iterdir():
 if p.is_dir(): shutil.rmtree(p)
 else: p.unlink()
''')


if __name__ == '__main__':
    main()
