"""Prepare only Vauxr's writable mount roots, never migrate existing contents."""
import os
from pathlib import Path
import stat


def initialize():
    for path in (Path('/data'), Path('/data/recordings'), Path('/data/firmware')):
        path.mkdir(exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise RuntimeError(f'Expected a storage directory: {path}')
        os.chown(path, 100, 101)
        # Retain existing group/other permissions; ensure the service can write.
        path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IRWXU)
    # These sibling services own their cache contents, not Vauxr.
    for name in ('piper', 'whisper'):
        (Path('/data') / name).mkdir(exist_ok=True)


if __name__ == '__main__':
    initialize()
