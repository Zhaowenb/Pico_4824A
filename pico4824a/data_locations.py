"""User-chosen read-only data locations; writes stay in the development project."""
from pathlib import Path
import json
import threading
from .storage_naming import output_path

_LOCK=threading.RLock()


def selected_directories(project):
    settings=project/'configs'/'data-locations.json'
    if not settings.is_file():return []
    raw=json.loads(settings.read_text(encoding='utf-8-sig'))
    if not isinstance(raw,dict):raise ValueError('data-locations.json 必须为 JSON 对象')
    directories=raw.get('directories',[])
    if not isinstance(directories,list) or not all(isinstance(p,str) and p.strip() for p in directories):
        raise ValueError('data-locations.json 的 directories 必须为文件夹路径列表')
    result=[]
    for value in directories:
        folder=Path(value.strip()).expanduser()
        if not folder.is_absolute():folder=project/folder
        folder=folder.resolve()
        if folder not in result:result.append(folder)
    return result


def readable_roots(project):
    project=project.resolve()
    roots=[project]
    reference=project.with_name('Pico_4824A')/'data'
    if reference.is_dir():roots.append(reference.resolve())
    return list(dict.fromkeys(roots+selected_directories(project)))


def is_readable_directory(path,project):
    path=Path(path).resolve()
    return any(path.is_relative_to(root) for root in readable_roots(project))


def remember_selected_directory(path,project):
    """Called only with a selection returned by the host's native picker."""
    path=Path(path).expanduser().resolve()
    if not path.is_dir():raise ValueError('所选数据目录不存在，可能已移除 U 盘')
    settings=output_path(project/'configs'/'data-locations.json')
    with _LOCK:
        folders=selected_directories(project)
        if not any(path.is_relative_to(root) for root in readable_roots(project)):
            folders.append(path)
            settings.parent.mkdir(parents=True,exist_ok=True)
            temporary=settings.with_suffix('.json.tmp')
            temporary.write_text(json.dumps({'directories':[str(p) for p in folders]},ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(settings)
    return path
