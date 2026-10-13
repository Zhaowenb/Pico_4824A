"""Read historical data and import working copies without writing to a reference."""
import hashlib
import json
from pathlib import Path
import shutil
import threading
from .storage_naming import PROJECT, output_path, readable_source

_LOCK = threading.RLock()
REFERENCE_DATA = PROJECT.with_name('Pico_4824A') / 'data'
LEGACY_INTERFERENCE = PROJECT.with_name('Pico_4824A_error') / 'data'


def import_directory(source, destination):
    source = Path(source).resolve()
    destination = output_path(destination)
    if not source.is_dir():
        raise ValueError('历史数据目录不存在')
    for item in source.rglob('*'):
        if item.is_symlink() or not item.resolve().is_relative_to(source):
            raise ValueError('历史数据目录包含链接，无法安全导入')
    with _LOCK:
        destination.mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(str(source).encode()).hexdigest()[:12]
        target = destination / (readable_source(source.name) + '__历史导入_' + identity)
        marker = target / 'import_origin.json'
        if marker.is_file():
            return target
        # An incomplete copy must never be mistaken for a completed import.
        temporary = destination / (target.name + '.importing')
        if temporary.exists():
            raise ValueError('上次导入未完成，请检查 .importing 目录后重试')
        shutil.copytree(source, temporary)
        (temporary / 'import_origin.json').write_text(json.dumps({'source': str(source), 'source_readonly': True}, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.rename(target)
        return target
