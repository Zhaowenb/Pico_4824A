"""Read-only file selection for the local scientific workstation."""
from pathlib import Path


def list_directory(supplied: str, project: Path) -> dict:
    roots = [project.resolve()]
    reference_data = project.with_name('Pico_4824A') / 'data'
    if reference_data.is_dir():
        roots.append(reference_data.resolve())
    candidate = Path(supplied).expanduser() if supplied.strip() else project / "data"
    path = (candidate if candidate.is_absolute() else project / candidate).resolve()
    if not any(path.is_relative_to(root) for root in roots):
        raise ValueError('请选择工作目录或参考数据目录内的文件；此浏览器仅用于读取数据。')
    if path.is_file():
        path = path.parent
    if not path.is_dir():
        raise ValueError('目录不存在，请检查路径。')
    entries = []
    for item in path.iterdir():
        if item.name.startswith('.') or item.name in {'__pycache__', 'node_modules'}:
            continue
        resolved = item.resolve()
        if not any(resolved.is_relative_to(root) for root in roots):
            continue
        try:
            folder = item.is_dir()
            if not folder and item.suffix.lower() not in {'.npz', '.csv', '.json'}:
                continue
            entries.append({'name': item.name, 'path': str(resolved), 'directory': folder,
                            'size': 0 if folder else item.stat().st_size})
        except OSError:
            continue
    entries.sort(key=lambda entry: (not entry['directory'], entry['name'].casefold()))
    parent = path.parent if any(path.parent.is_relative_to(root) for root in roots) else None
    return {'path': str(path), 'parent': str(parent) if parent else None, 'entries': entries,
            'roots': [{'name': '工作目录', 'path': str(roots[0])},
                      {'name': '采集数据', 'path': str((project / 'data').resolve())}] +
                     ([{'name': '参考数据 · 只读', 'path': str(roots[1])}] if len(roots) > 1 else [])}
