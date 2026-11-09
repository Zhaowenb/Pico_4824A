"""Read-only file selection for the local scientific workstation."""
from pathlib import Path
from .data_locations import readable_roots, selected_directories


def list_directory(supplied: str, project: Path) -> dict:
    roots = readable_roots(project)
    supplied=supplied.strip()
    if len(supplied)>1 and supplied[0]==supplied[-1] and supplied[0] in {'"',"'"}:supplied=supplied[1:-1]
    default=project/'data' if (project/'data').is_dir() else project
    candidate = Path(supplied).expanduser() if supplied else default
    path = (candidate if candidate.is_absolute() else project / candidate).resolve()
    if not any(path.is_relative_to(root) for root in roots):
        raise ValueError('请点“其他位置 / U盘”，通过系统窗口选择该数据文件夹后再读取。')
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
            'roots': [{'name': '工作目录', 'path': str(project.resolve())},
                      {'name': '采集数据', 'path': str((project / 'data').resolve())}] +
                     [{'name':'外部数据 · '+(folder.name or folder.anchor), 'path':str(folder)} for folder in selected_directories(project)] +
                     ([{'name':'参考数据 · 只读','path':str((project.with_name('Pico_4824A')/'data').resolve())}]
                      if (project.with_name('Pico_4824A')/'data').is_dir() else [])}
