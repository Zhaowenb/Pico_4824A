"""Target-only HTTP regression checks for the isolated file-analysis page."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.environ.get("WAVEGUARD_BASE_URL", "http://127.0.0.1:54824")
DATA_FILE = (ROOT / "data" / "A1.npz").resolve()
SIGNATURE_FILE = ROOT / "tests" / "file-analysis-baseline-signatures.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def request_json(path: str, payload: dict | None = None) -> dict:
    if payload is None:
        request = Request(f"{BASE_URL}{path}")
    else:
        request = Request(
            f"{BASE_URL}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    try:
        with urlopen(request, timeout=180) as response:
            body = response.read()
            if response.status != 200:
                raise AssertionError(f"{path}: HTTP {response.status}")
            return json.loads(body)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise AssertionError(f"{path}: HTTP {error.code}: {detail}") from error


def post(path: str, payload: dict) -> dict:
    return request_json(path, payload)


def record(signatures: dict, name: str, payload: dict) -> dict:
    digest = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    signatures[name] = digest
    return payload


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "capture"
    if not DATA_FILE.is_file():
        raise AssertionError(f"TARGET fixture missing: {DATA_FILE}")
    original_hash = sha256(DATA_FILE.read_bytes())
    signatures: dict[str, str] = {}

    page = urlopen(f"{BASE_URL}/file-analysis", timeout=30)
    html = page.read().decode("utf-8")
    assert page.status == 200 and "data-pages=\"file-analysis\"" in html
    for asset in ("/app.js", "/styles.css"):
        with urlopen(f"{BASE_URL}{asset}", timeout=30) as response:
            assert response.status == 200 and len(response.read()) > 1000
    signatures["page-route"] = sha256(html.encode("utf-8"))

    browse = record(signatures, "browse", post("/api/analysis/browse", {"path": str(DATA_FILE)}))
    assert browse["kind"] == "file" and browse["files"] == [str(DATA_FILE)]

    initial = record(signatures, "load-initial", post("/api/analysis/process", {
        "path": str(DATA_FILE), "channels": [],
        "filter": {"enabled": False, "low_hz": 60_000, "high_hz": 90_000, "transition_hz": 5_000},
        "show_raw": True, "show_filtered": False,
        "spectrum": {"source": "filtered", "mode": "amplitude", "window_function": "hann",
                     "frequency_min_hz": 0, "frequency_max_hz": 20_000_000, "windows": []},
    }))
    metadata = initial["metadata"]
    assert metadata["samples"] == 40_000 and metadata["channels"] == ["A", "B", "C", "H"]
    assert set(initial["waveform"]) == {f"{channel}:raw" for channel in metadata["channels"]}
    start_us = metadata["time_start_s"] * 1e6
    end_us = metadata["time_end_s"] * 1e6

    base_payload = {
        "path": str(DATA_FILE), "channels": metadata["channels"],
        "filter": {"enabled": False, "low_hz": 60_000, "high_hz": 90_000, "transition_hz": 5_000},
        "show_raw": True, "show_filtered": False,
        "spectrum": {"source": "raw", "mode": "amplitude", "window_function": "hann",
                     "frequency_min_hz": 0, "frequency_max_hz": 200_000,
                     "windows": [{"label": "全记录", "start_us": start_us, "end_us": end_us, "color": "#2f73ff"}]},
        "time_start_us": start_us, "time_end_us": end_us,
    }
    raw = record(signatures, "raw-process", post("/api/analysis/process", base_payload))
    assert "A:raw" in raw["waveform"] and "A:filtered" not in raw["waveform"]
    assert raw["spectra"] and raw["spectra"][0]["frequency_hz"]

    selected_payload = json.loads(json.dumps(base_payload))
    selected_payload["channels"] = ["B", "H"]
    selected = post("/api/analysis/process", selected_payload)
    assert set(selected["waveform"]) == {"B:raw", "H:raw"}
    assert set(selected["spectra"][0]["channels"]) == {"B", "H"}

    filtered_payload = json.loads(json.dumps(base_payload))
    filtered_payload["filter"]["enabled"] = True
    filtered_payload["show_filtered"] = True
    filtered_payload["spectrum"]["source"] = "filtered"
    filtered = record(signatures, "filtered-process", post("/api/analysis/process", filtered_payload))
    assert "A:filtered" in filtered["waveform"] and len(filtered["waveform"]["A:filtered"]) > 100

    preview = record(signatures, "run-preview", post("/api/analysis/run-preview", {"path": str(DATA_FILE), "max_points": 4000}))
    assert preview["metadata"]["channels"] == metadata["channels"] and len(preview["time_s"]) <= 4000

    shared_tf = {
        "path": str(DATA_FILE), "channel": "A", "filter": {"enabled": False},
        "start_us": start_us, "end_us": end_us,
        "frequency_min_hz": 20_000, "frequency_max_hz": 180_000, "floor_db": -60,
        "window_us": 80, "overlap_ratio": .85, "fft_samples": 0, "window_function": "hann",
        "wpd_level": 0, "wavelet": "db4", "cwt_bins": 72, "morlet_omega0": 6,
        "cwt_log_frequency": False,
    }
    for method in ("stft", "wpd", "cwt"):
        result = record(signatures, f"time-frequency-{method}", post(
            "/api/analysis/time-frequency", {**shared_tf, "method": method}
        ))
        assert result["method"] == method and result["time_us"] and result["frequency_hz"]
        assert result["values_db"] and result["details"]

    experimental = record(signatures, "experimental", post("/api/analysis/experimental-modes", {
        "path": str(DATA_FILE), "reference_channel": "A", "comparison_channel": "B",
        "filter": {"enabled": False}, "direct_start_us": 150, "direct_end_us": 270,
        "search_start_us": 270, "search_end_us": 620, "end_start_us": 630,
        "end_end_us": 760, "maximum_shift_us": 2, "temporal_window_us": 20,
        "minimum_separation_us": 30, "candidate_count": 6,
    }))
    assert experimental["status"] == "experimental_test_only" and "metrics" in experimental

    for output_format in ("npz", "csv"):
        exported = record(signatures, f"export-{output_format}", post("/api/analysis/export", {
            "path": str(DATA_FILE), "format": output_format,
            "filter": {"low_hz": 60_000, "high_hz": 90_000, "transition_hz": 5_000},
        }))
        output = Path(exported["path"]).resolve()
        assert output.is_file() and ROOT in output.parents
        if output_format == "csv":
            assert output.with_suffix(".json").is_file()

    assert sha256(DATA_FILE.read_bytes()) == original_hash, "raw target fixture was modified"
    if mode == "capture":
        SIGNATURE_FILE.write_text(json.dumps(signatures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif mode == "compare":
        expected = json.loads(SIGNATURE_FILE.read_text(encoding="utf-8"))
        actual_api = {name: value for name, value in signatures.items() if name != "page-route"}
        expected_api = {name: value for name, value in expected.items() if name != "page-route"}
        assert actual_api == expected_api, "API response signatures changed after visual refactor"
    else:
        raise ValueError("mode must be capture or compare")

    api_signature_count = len([name for name in signatures if name != "page-route"])
    print(f"PASS: route/assets, browse/load, channel subset, raw/filtered, FFT, STFT/WPD/CWT, Experimental, exports; {api_signature_count} API signatures ({mode}).")
    print(f"PASS: target fixture unchanged; exports remain under {ROOT}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
