"""Persistent, full-resolution analysis for the independent interference lab."""

from __future__ import annotations

from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import re
import threading
from uuid import uuid4

import numpy as np

from .config import AcquisitionConfig
from .storage import load_npz, save_npz
from .storage_naming import session_directory, readable_source


ROOT = Path(__file__).resolve().parent.parent / "data" / "interference"
_LOCK = threading.RLock()
_ID = re.compile(r"^[0-9a-f]{32}$")


def _folder(session_id: str, root: Path = ROOT) -> Path:
    if not _ID.fullmatch(session_id):
        raise ValueError("invalid session id")
    legacy = root / session_id
    if legacy.exists():
        return legacy
    for path in root.glob('*/session.json'):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            continue
        try:
            if json.loads(path.read_text(encoding='utf-8')).get('id') == session_id:
                return path.parent
        except (ValueError, OSError):
            continue
    return legacy


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def create_session(payload: dict, root: Path = ROOT) -> dict:
    config = AcquisitionConfig.from_dict(payload["config"])
    if config.awg.frequency_hz != 75_000 or config.awg.waveform != "hann_burst":
        raise ValueError("experiment requires a 75 kHz Hann burst")
    repeats = int(payload.get("repeats", 3))
    if repeats < 3 or repeats > 5:
        raise ValueError("repeats must be 3..5")
    session_id = uuid4().hex
    session = {
        "id": session_id, "created_at": datetime.now(timezone.utc).isoformat(),
        "config": config.to_dict(), "repeats": repeats,
        "channels": payload["channels"], "windows_us": payload["windows_us"],
        "runs": [], "completed_experiments": [],
    }
    folder = session_directory(root, 'interference')
    session['name'] = folder.name
    session['output_dir'] = str(folder)
    _write_json(folder / "session.json", session)
    return session


def load_session(session_id: str, root: Path = ROOT) -> dict:
    path = _folder(session_id, root) / "session.json"
    if not path.is_file():
        raise ValueError("session not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _slice(t: np.ndarray, a_us: float, b_us: float) -> np.ndarray:
    if not np.isfinite([a_us, b_us]).all() or a_us >= b_us:
        raise ValueError("invalid window")
    dt = float(t[1] - t[0]) if t.size > 1 else 0.0
    if a_us * 1e-6 < t[0] - dt*.5 or b_us * 1e-6 > t[-1] + dt*1.5:
        raise ValueError(f"window {a_us:g}..{b_us:g} us exceeds recorded time")
    indices = np.flatnonzero((t >= a_us * 1e-6) & (t < b_us * 1e-6))
    if indices.size < 4:
        raise ValueError(f"window {a_us:g}..{b_us:g} us has fewer than four samples")
    return indices


def _stats(x: np.ndarray, fs: float, frequency: float = 75_000) -> dict:
    n = x.size
    k = np.arange(n)
    z = np.sum(x * np.exp(-2j * np.pi * frequency * k / fs)) * (2 / n)
    spectrum = np.fft.rfft(x)
    amplitude = np.abs(spectrum) * (2 / n)
    amplitude[0] *= .5
    if n % 2 == 0:
        amplitude[-1] *= .5
    return {
        "peak_v": float(np.max(np.abs(x))),
        "rms_v": float(np.sqrt(np.mean(x * x))),
        "energy_v2s": float(np.sum((x[:-1] ** 2 + x[1:] ** 2) * .5) / fs),
        "a75_v": float(abs(z)), "phi75_rad": float(np.angle(z)),
        "fft_hz": np.fft.rfftfreq(n, 1 / fs).tolist(),
        "fft_amplitude_v": amplitude.tolist(),
        "fft_phase_rad": np.angle(spectrum).tolist(),
    }


def analyze(capture, settings: dict) -> dict:
    t = capture.time_s
    fs = capture.actual_sample_rate_hz
    channels = settings["channels"]
    rx_name = channels["rx"]
    if rx_name not in capture.volts:
        raise ValueError(f"RX channel {rx_name} was not acquired")
    windows = settings["windows_us"]
    t0 = float(windows["t0"])
    duration = capture.config.awg.cycles / 75_000 * 1e6
    tail_end = float(windows["tail_end"])
    baseline = _slice(t, t0 + float(windows["baseline_start"]), t0 + float(windows["baseline_end"]))
    tx = _slice(t, t0, t0 + duration)
    tail = _slice(t, t0 + duration, t0 + duration + tail_end)
    echo = None
    if windows.get("echo_start") not in (None, "") and windows.get("echo_end") not in (None, ""):
        echo = _slice(t, t0 + float(windows["echo_start"]), t0 + float(windows["echo_end"]))
    scale = float(channels.get("rx_probe", 1))
    if scale <= 0:
        raise ValueError("invalid RX probe multiplier")
    raw = capture.volts[rx_name] * scale
    baseline_dc = float(np.mean(raw[baseline]))
    x = raw - baseline_dc
    out = {
        "baseline_dc_v": baseline_dc,
        "noise_rms_v": float(np.sqrt(np.mean(x[baseline] ** 2))),
        "tx": _stats(x[tx], fs), "tail": _stats(x[tail], fs),
        "echo": _stats(x[echo], fs) if echo is not None else None,
        "overflow_channels": list(capture.overflow_channels),
        "simulated": capture.simulated,
    }
    if echo is not None and out["noise_rms_v"] > 0 and out["echo"]["peak_v"] > 0:
        out["snr_echo_db"] = float(20 * np.log10(out["echo"]["peak_v"] / out["noise_rms_v"]))
    else:
        out["snr_echo_db"] = None
    rod_name = channels.get("rod")
    if rod_name and rod_name in capture.volts and settings.get("experiment") == 3:
        probe = float(channels.get("rod_probe", 10))
        rod = capture.volts[rod_name] * probe
        rod -= np.mean(rod[baseline])
        relations = {}
        for label, indices in (("tx", tx), ("tail", tail)):
            a, b = x[indices], rod[indices]
            rho = float(np.corrcoef(a, b)[0, 1]) if np.std(a) and np.std(b) else None
            if rho is not None and not np.isfinite(rho):
                rho = None
            ar, br = _stats(a, fs), _stats(b, fs)
            relations[label] = {"rho": rho,
                "a75_ratio": ar["a75_v"] / br["a75_v"] if br["a75_v"] else None,
                "phase_difference_rad": float(np.angle(np.exp(1j * (ar["phi75_rad"] - br["phi75_rad"]))))}
        out["rod_relation"] = relations
    return out


def _mean_sd(values: list[float]) -> dict:
    a = np.asarray(values, dtype=float)
    return {"mean": float(np.mean(a)), "sd": float(np.std(a, ddof=1)) if len(a) > 1 else 0.0, "n": len(a)}


def summarize(session: dict) -> dict:
    groups: dict[str, list[dict]] = {}
    for run in session["runs"]:
        key = f'{run["experiment"]:02d}:{run["condition"]}'
        groups.setdefault(key, []).append(run)
    rows = []
    for key, runs in groups.items():
        metrics = {}
        for region in ("tx", "tail", "echo"):
            for field in ("peak_v", "rms_v", "energy_v2s", "a75_v"):
                values = [r["metrics"][region][field] for r in runs if r["metrics"].get(region)]
                if values:
                    metrics[f"{region}_{field}"] = _mean_sd(values)
        snr = [r["metrics"]["snr_echo_db"] for r in runs if r["metrics"].get("snr_echo_db") is not None]
        if snr:
            metrics["snr_echo_db"] = _mean_sd(snr)
        rows.append({"key": key, "experiment": runs[0]["experiment"], "condition": runs[0]["condition"],
                     "variables": runs[0]["variables"], "repeats": len(runs), "metrics": metrics,
                     "overflow": any(r["metrics"]["overflow_channels"] for r in runs)})
    fits = _fits(rows)
    return {"rows": rows, "fits": fits, "comparisons": _comparisons(rows),
            "conclusions": _conclusions(rows, session["runs"], fits),
            "evidence": _evidence(rows, session["runs"])}


def _comparisons(rows: list[dict]) -> list[dict]:
    outcomes=[]
    pairs=[(1,"float","grounded"),(5,"normal","twisted"),(5,"normal","shielded_twisted"),
           (8,"real","dummy"),(9,"normal","no_mech"),(10,"common","star"),(12,"no_shield","shield")]
    for exp,left,right in pairs:
        a=next((r for r in rows if r["experiment"]==exp and r["condition"]==left),None)
        b=next((r for r in rows if r["experiment"]==exp and r["condition"]==right),None)
        if a and b:
            fields={}
            for key in ("tx_peak_v","tail_peak_v","tx_energy_v2s","tail_energy_v2s","echo_peak_v"):
                x=a["metrics"].get(key,{}).get("mean")
                y=b["metrics"].get(key,{}).get("mean")
                if x is not None and y is not None and x>0 and y>0:
                    fields[key]={"ratio":x/y,"suppression_db":float((10 if "energy" in key else 20)*np.log10(x/y))}
            outcomes.append({"experiment":exp,"left":left,"right":right,"metrics":fields})
    distances={}
    for r in rows:
        if r["experiment"]==4 and "distance_cm" in r["variables"]:
            distance=float(r["variables"]["distance_cm"])
            state="grounded" if r["condition"].endswith("_grounded") else "float"
            distances.setdefault(distance,{})[state]=r
    for distance,pair in distances.items():
        if "float" in pair and "grounded" in pair:
            fields={}
            for key in ("tx_peak_v","tail_peak_v","tx_energy_v2s","tail_energy_v2s"):
                x=pair["float"]["metrics"].get(key,{}).get("mean")
                y=pair["grounded"]["metrics"].get(key,{}).get("mean")
                if x is not None and y is not None and x>0 and y>0:
                    fields[key]={"delta":x-y,"ratio":x/y,"suppression_db":float((10 if "energy" in key else 20)*np.log10(x/y))}
            outcomes.append({"experiment":4,"distance_cm":distance,"left":"float","right":"grounded","metrics":fields})
    return outcomes


def _conclusions(rows: list[dict], runs: list[dict], fits: dict) -> dict:
    text={str(i):"证据不足：尚需完成相应条件与重复采集。" for i in range(1,13)}
    comparisons=_comparisons(rows)
    for c in comparisons:
        exp=c["experiment"]
        tx=c["metrics"].get("tx_peak_v",{}).get("suppression_db")
        if tx is not None and exp!=4:
            label="改善" if tx>=6 else "未观察到明确改善"
            text[str(exp)]=f"发射峰值抑制 {tx:.2f} dB；{label}。需结合回波变化和其他对照判别耦合路径。"
            if exp==1:
                echo=c["metrics"].get("echo_peak_v",{}).get("ratio")
                if echo and echo>1.5:
                    text["1"]+=" 接棒后回波也明显下降，可能同时改变了传感器机械或电气边界。"
    rod=[r["metrics"].get("rod_relation",{}).get("tx",{}).get("rho") for r in runs if r["experiment"]==3]
    rod=[x for x in rod if x is not None]
    if rod:
        text["3"]=f"发射窗棒/RX 平均相关系数 {np.mean(rod):.3f}；高相关支持共模相关性，探头负载仍需考虑。"
    for exp,key,description in ((4,"distance","距离幂律"),(6,"area","回路面积线性"),(7,"angle","角度余弦"),(11,"ground_length","公共地长度线性")):
        fit=fits.get(key)
        if fit:
            r2=fit.get("r2")
            text[str(exp)]=f"{description}拟合 R²={r2:.3f}；"+("趋势较一致，可作为耦合证据。" if r2>=.8 else "拟合较弱，暂不据此判别。") if r2 is not None else "数据变化不足，无法评价拟合质量。"
    if any(c["experiment"]==4 for c in comparisons):
        count=sum(c["experiment"]==4 for c in comparisons)
        text["4"]+=f" 已配对 {count} 个距离的浮空/接棒结果。"
    for exp in (2,):
        if "r50_ohm" in fits: text[str(exp)]=f"估计 R₅₀={fits['r50_ohm']:.3g} Ω；随电阻增大的趋势需结合浮空基准检查。"
    return text


def _fit_linear(x: np.ndarray, y: np.ndarray) -> dict | None:
    if len(x) < 3 or len(np.unique(x)) < 3:
        return None
    a, b = np.polyfit(x, y, 1)
    predicted = a*x + b
    total = np.sum((y-y.mean())**2)
    return {"k": float(a), "b": float(b), "r2": float(1-np.sum((y-predicted)**2)/total) if total else None}


def _fit_basis(x: np.ndarray, y: np.ndarray, candidates) -> tuple | None:
    best = None
    for parameter, basis in candidates:
        if not np.isfinite(basis).all() or np.ptp(basis) < 1e-12:
            continue
        a, b = np.linalg.lstsq(np.column_stack([basis, np.ones(len(basis))]), y, rcond=None)[0]
        if a < 0 or b < 0:
            continue
        error = float(np.sum((y-a*basis-b)**2))
        if best is None or error < best[0]:
            best = (error, float(parameter), float(a), float(b))
    if best is None:
        return None
    total = float(np.sum((y-y.mean())**2))
    return best[1], best[2], best[3], (1-best[0]/total) if total else None


def _fits(rows: list[dict]) -> dict:
    fits = {}
    def pairs(exp, condition=None, variable="value"):
        a = [(r["variables"].get(variable), r["metrics"].get("tx_peak_v", {}).get("mean"))
             for r in rows if r["experiment"] == exp and (condition is None or r["condition"] == condition or r["condition"].endswith("_"+condition))]
        return [(float(x), float(y)) for x,y in a if x is not None and y is not None]
    p = pairs(2, variable="resistance_ohm")
    if len(p) >= 3:
        p.sort()
        floating = next((r["metrics"]["tx_peak_v"]["mean"] for r in rows if r["experiment"] == 1 and r["condition"] == "float" and "tx_peak_v" in r["metrics"]), None)
        if floating is not None:
            target = (p[0][1] + floating)/2
            for (x0,y0),(x1,y1) in zip(p,p[1:]):
                if x0 > 0 and (y0-target)*(y1-target) <= 0 and y0 != y1:
                    fits["r50_ohm"] = float(np.exp(np.log(x0)+(target-y0)/(y1-y0)*(np.log(x1)-np.log(x0))))
                    break
    for exp, variable, key in ((6,"area_cm2","area"),(11,"length_cm","ground_length")):
        p = pairs(exp, variable=variable)
        if p:
            fit = _fit_linear(np.array([x for x,_ in p]),np.array([y for _,y in p]))
            if fit: fits[key] = fit
    p = pairs(4,"grounded","distance_cm")
    if len(p) >= 4 and all(x > 0 for x,_ in p):
        x,y = np.array([x for x,_ in p]),np.array([y for _,y in p])
        found=_fit_basis(x,y,((n,x**(-n)) for n in np.linspace(.1,8,790)))
        if found:
            n,a,b,r2=found
            fits["distance"]={"a":a,"n":n,"b":b,"r2":r2}
    p = pairs(7,variable="angle_deg")
    if len(p)>=4:
        x,y=np.array([x for x,_ in p]),np.array([y for _,y in p])
        found=_fit_basis(x,y,((theta,np.abs(np.cos(np.deg2rad(x-theta)))) for theta in np.linspace(-90,90,721)))
        if found:
            theta,a,b,r2=found
            fits["angle"]={"a":a,"theta0_deg":theta,"b":b,"r2":r2}
    return fits


def _evidence(rows: list[dict], runs: list[dict]) -> dict:
    def peak(exp, condition):
        return next((r["metrics"]["tx_peak_v"]["mean"] for r in rows if r["experiment"]==exp and r["condition"]==condition and not r["overflow"] and "tx_peak_v" in r["metrics"]),None)
    result={key:[] for key in ("electric","magnetic","common","mechanical")}
    checks=(("electric",1,"float","grounded"),("magnetic",5,"normal","twisted"),
            ("common",10,"common","star"),("electric",12,"no_shield","shield"))
    for category,exp,a,b in checks:
        x,y=peak(exp,a),peak(exp,b)
        if x is not None and y and x/y>=2: result[category].append(f"实验{exp:02d} 抑制至少 6 dB")
    for run in runs:
        rel=run["metrics"].get("rod_relation",{}).get("tx",{})
        if rel.get("rho") is not None and abs(rel["rho"])>=.7:
            result["electric"].append("棒与 RX 发射窗相关 |ρ|≥0.7");break
    p=[]
    for r in rows:
        if r["experiment"]==2 and "resistance_ohm" in r["variables"] and "tx_peak_v" in r["metrics"]:
            p.append((float(r["variables"]["resistance_ohm"]),r["metrics"]["tx_peak_v"]["mean"]))
    if len(p)>=3 and max(y for _,y in p)>min(y for _,y in p)*2 and sorted(p)[-1][1]>sorted(p)[0][1]:
        result["electric"].append("串联电阻增大时串扰恢复")
    fits=_fits(rows)
    distance=fits.get("distance")
    if distance and distance["n"]>1 and distance["r2"] is not None and distance["r2"]>=.8:
        result["magnetic"].append("接棒残余随距离快速衰减")
    area=fits.get("area")
    if area and area["k"]>0 and area["r2"] is not None and area["r2"]>=.8:
        result["magnetic"].append("串扰随接收回路面积增加")
    angle=fits.get("angle")
    if angle and angle["r2"] is not None and angle["r2"]>=.8 and angle["a"]>angle["b"]*.25:
        result["magnetic"].append("接收回路呈角度依赖")
    length=fits.get("ground_length")
    if length and length["k"]>0 and length["r2"] is not None and length["r2"]>=.8:
        result["common"].append("共享地长度增大时串扰上升")
    normal_echo=next((r["metrics"].get("echo_peak_v",{}).get("mean") for r in rows if r["experiment"]==9 and r["condition"]=="normal"),None)
    no_mech_echo=next((r["metrics"].get("echo_peak_v",{}).get("mean") for r in rows if r["experiment"]==9 and r["condition"]=="no_mech"),None)
    if normal_echo and no_mech_echo is not None and no_mech_echo/normal_echo<.5:
        result["mechanical"].append("去机械耦合后晚到回波下降")
    real_echo=next((r["metrics"].get("echo_peak_v",{}).get("mean") for r in rows if r["experiment"]==8 and r["condition"]=="real"),None)
    dummy_echo=next((r["metrics"].get("echo_peak_v",{}).get("mean") for r in rows if r["experiment"]==8 and r["condition"]=="dummy"),None)
    if real_echo and dummy_echo is not None and dummy_echo/real_echo<.5:
        result["mechanical"].append("Dummy PZT 的晚到回波下降")
    labels={0:"未观察到明显证据",1:"弱证据",2:"中等证据",3:"强证据"}
    return {k:{"level":labels[min(len(set(v)),3)],"reasons":list(dict.fromkeys(v))} for k,v in result.items()}


def save_run(session_id: str, capture_id: int, capture, condition: dict, root: Path = ROOT) -> dict:
    with _LOCK:
        session=load_session(session_id,root)
        if any(r["capture_id"]==capture_id for r in session["runs"]):
            raise ValueError("capture was already saved in this session")
        if capture.config.to_dict()!=session["config"]:
            raise ValueError("capture configuration differs from locked session")
        experiment=int(condition["experiment"])
        if experiment not in range(1,13): raise ValueError("invalid experiment")
        name=str(condition["condition"])
        if sum(r["experiment"]==experiment and r["condition"]==name for r in session["runs"])>=session["repeats"]:
            raise ValueError("this condition already has the required repetitions")
        chosen_windows=condition.get("windows_us", session["windows_us"])
        if {k:v for k,v in chosen_windows.items() if k!="t0"} != {k:v for k,v in session["windows_us"].items() if k!="t0"}:
            raise ValueError("analysis windows changed; reanalyze the session before saving another run")
        settings={"experiment":experiment,"channels":session["channels"],"windows_us":condition.get("windows_us",session["windows_us"])}
        metrics=analyze(capture,settings)
        run_id=uuid4().hex
        folder=_folder(session_id,root)/"runs"
        folder.mkdir(parents=True, exist_ok=True)
        spectra={}
        for region in ("tx","tail","echo"):
            if metrics[region]:
                for field in ("fft_hz","fft_amplitude_v","fft_phase_rad"):
                    spectra[f"{region}_{field}"]=np.asarray(metrics[region].pop(field))
        repeat = 1 + sum(r['experiment']==experiment and r['condition']==name for r in session['runs'])
        stem = f"实验{experiment:02d}__{readable_source(name)}__重复{repeat:02d}__记录{len(session['runs'])+1:03d}"
        np.savez_compressed(folder/f"{stem}__FFT.npz",**spectra)
        path=folder/f"{stem}__原始波形.npz"
        save_npz(capture,path)
        run={"id":run_id,"capture_id":capture_id,"timestamp":datetime.now(timezone.utc).isoformat(),
             "experiment":experiment,"condition":str(condition["condition"]),
             "variables":dict(condition.get("variables",{})),"notes":str(condition.get("notes","")),
             "windows_us":settings["windows_us"],"raw_file":f"runs/{stem}__原始波形.npz",
             "fft_file":f"runs/{stem}__FFT.npz","metrics":metrics}
        session["runs"].append(run)
        _write_json(_folder(session_id,root)/"session.json",session)
        return {"run":run,"summary":summarize(session)}


def reanalyze(session_id: str, windows: dict, root: Path = ROOT) -> dict:
    with _LOCK:
        session=load_session(session_id,root)
        for run in session["runs"]:
            capture=load_npz(_folder(session_id,root)/run["raw_file"])
            run["windows_us"]=windows
            metrics=analyze(capture,{"experiment":run["experiment"],"channels":session["channels"],"windows_us":windows})
            spectra={}
            for region in ("tx","tail","echo"):
                if metrics[region]:
                    for field in ("fft_hz","fft_amplitude_v","fft_phase_rad"):
                        spectra[f"{region}_{field}"]=np.asarray(metrics[region].pop(field))
            np.savez_compressed(_folder(session_id,root)/run["fft_file"],**spectra)
            run["metrics"]=metrics
        session["windows_us"]=windows
        _write_json(_folder(session_id,root)/"session.json",session)
        return {"session":session,"summary":summarize(session)}


def export_csv(session_id: str, root: Path = ROOT) -> Path:
    session=load_session(session_id,root)
    target=_folder(session_id,root)/"runs.csv"
    with target.open("w",encoding="utf-8-sig",newline="") as file:
        writer=csv.writer(file)
        writer.writerow(["run_id","experiment","condition","timestamp","variables_json","notes","tx_peak_v","tail_peak_v","echo_peak_v","snr_echo_db","raw_file"])
        for r in session["runs"]:
            m=r["metrics"]
            writer.writerow([r["id"],r["experiment"],r["condition"],r["timestamp"],json.dumps(r["variables"],ensure_ascii=False),r["notes"],m["tx"]["peak_v"],m["tail"]["peak_v"],m["echo"]["peak_v"] if m["echo"] else "",m["snr_echo_db"],r["raw_file"]])
    return target


def preview(session_id: str, run_id: str, root: Path = ROOT) -> dict:
    session=load_session(session_id,root)
    run=next((r for r in session["runs"] if r["id"]==run_id),None)
    if run is None: raise ValueError("run not found")
    capture=load_npz(_folder(session_id,root)/run["raw_file"])
    step=max(1,int(np.ceil(capture.samples/4000)))
    with np.load(_folder(session_id,root)/run["fft_file"],allow_pickle=False) as fft:
        spectra={}
        for region in ("tx","tail","echo"):
            key=f"{region}_fft_hz"
            if key in fft:
                stride=max(1,int(np.ceil(len(fft[key])/2000)))
                spectra[region]={field:fft[f"{region}_{field}"][::stride].tolist()
                    for field in ("fft_hz","fft_amplitude_v","fft_phase_rad")}
    return {"time_s":capture.time_s[::step].tolist(),"channels":{k:v[::step].tolist() for k,v in capture.volts.items()},"spectra":spectra}
