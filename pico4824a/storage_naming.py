"""One naming policy for all saved measurements; no scientific transformations."""
from datetime import datetime
import json
from pathlib import Path
import re
import threading

PROJECT = Path(__file__).resolve().parent.parent
SETTINGS = PROJECT / 'configs' / 'storage-naming.json'
DEFAULTS = {
    'capture': '实时测量', 'sweep': '参数扫描', 'lcr': 'LCR测量',
    'big_lcr': '大信号LCR', 'linearity': 'LCR线性度',
    'interference': '干扰实验', 'analysis': '单数据分析', 'bias': '偏置电流扫描',
}
_LOCK = threading.RLock()


def output_path(path):
    target=Path(path).expanduser().resolve()
    if not target.is_relative_to(PROJECT):
        raise ValueError('保存文件必须位于 Pico_4824A_btf，参考仓库只读')
    return target


def component(value):
    """Reject unsafe Windows names rather than silently changing user labels."""
    if not isinstance(value, str):
        raise ValueError('保存名称必须是文字')
    value = value.strip()
    if not value or len(value) > 64 or re.search(r'[<>:"/\\|?*\x00-\x1f]', value):
        raise ValueError('名称需要 1–64 字，不能包含路径或 Windows 文件名禁用字符')
    if value in {'.', '..'} or value.endswith(('.', ' ')) or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?', value, re.I):
        raise ValueError('名称不能使用 Windows 保留名称')
    return value


def preferences():
    with _LOCK:
        values = dict(DEFAULTS)
        if SETTINGS.is_file():
            raw = json.loads(SETTINGS.read_text(encoding='utf-8'))
            for key in DEFAULTS:
                if key in raw: values[key] = component(raw[key])
        return values


def save_preferences(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise ValueError('命名设置包含未知字段')
    with _LOCK:
        values = preferences()
        values.update({key: component(value) for key, value in raw.items()})
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(SETTINGS)
        return values


def session_directory(root, kind, details='', name=None, simulated=None):
    root = Path(root).resolve()
    if not root.is_relative_to(PROJECT):
        raise ValueError('保存目录必须位于 Pico_4824A_btf')
    label = component(name) if name else preferences()[kind]
    stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    parts = [label, stamp]
    if simulated is not None: parts.append('仿真' if simulated else '实测')
    if details:
        # Details are produced by code, never interpreted as paths.
        parts.append(component(details))
    base = '__'.join(parts)
    if len(str(root / base)) > 180:
        raise ValueError('任务名称过长，请缩短名称，为电流档和波形文件保留路径空间')
    root.mkdir(parents=True, exist_ok=True)
    for sequence in range(1, 10000):
        directory = root / (base if sequence == 1 else f'{base}__第{sequence:02d}次')
        try:
            directory.mkdir()
            return directory
        except FileExistsError:
            continue
    raise RuntimeError('同名任务数量过多，请更换保存名称')


def capture_stem(result, root=None):
    directory = session_directory(root or PROJECT/'data/captures', 'capture', simulated=result.simulated)
    channels = '-'.join(result.volts)
    excitation=f'{result.config.awg.frequency_hz/1000:g}kHz' if result.config.awg.enabled else 'AWG关闭'
    return directory / f'CH-{channels}__{result.actual_sample_rate_hz/1e6:g}MSps__{excitation}__原始波形'


def readable_source(stem):
    # Existing input names may contain characters unsuitable for output names.
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(stem)).strip(' .')[:48]
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?', value, re.I):
        value = '信号_' + value
    return component(value or '信号')
