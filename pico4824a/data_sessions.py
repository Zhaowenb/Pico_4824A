"""Persistent identities for multi-format exports; scientific calculations stay in their modules."""
import hashlib
import json
from pathlib import Path
import threading
from .storage_naming import PROJECT, session_directory, output_path
from zipfile import ZipFile, ZIP_DEFLATED

_LOCK = threading.RLock()


def archive_session(folder):
    """Archive every file in a saved job without changing its scientific content."""
    folder = output_path(folder)
    if not folder.is_dir(): raise ValueError('任务目录不存在')
    with _LOCK:
        files = sorted(p for p in folder.rglob('*') if p.is_file())
        if not files: raise ValueError('任务目录没有数据')
        if any(p.is_symlink() or not p.resolve().is_relative_to(folder) for p in folder.rglob('*')):
            raise ValueError('任务目录包含链接，不能打包')
        destination = output_path(folder.parent / 'exports')
        destination.mkdir(exist_ok=True)
        archive = destination / (folder.name+'.zip')
        temporary = archive.with_suffix('.zip.tmp')
        try:
            with ZipFile(temporary, 'w', compression=ZIP_DEFLATED) as stream:
                for file in files:
                    stream.write(file, (Path(folder.name)/file.relative_to(folder)).as_posix())
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
        return archive


def analysis_session(source, processing):
    source = Path(source).resolve()
    identity = {'source': str(source), 'size': source.stat().st_size,
                'mtime_ns': source.stat().st_mtime_ns, 'processing': processing}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    root = PROJECT / 'data/analysis_exports'
    with _LOCK:
        # Reuse a persisted identity across NPZ/CSV requests and server restarts.
        for marker in root.glob('*/data_session.json'):
            if not marker.resolve().is_relative_to(PROJECT): continue
            try:
                if json.loads(marker.read_text(encoding='utf-8')).get('identity') == key:
                    return output_path(marker.parent)
            except (ValueError, OSError):
                continue
        directory = session_directory(root, 'analysis')
        manifest = {'schema': 'waveguard-data-session-v1', 'identity': key,
                    'input': identity, 'original_modified': False}
        (directory/'data_session.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        return directory
