"""Command-line interface for scripted captures."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .config import AcquisitionConfig
from .device import Pico4824A, PicoError
from .storage import save_csv, save_npz
from .storage_naming import capture_stem


PROJECT_DIR = Path(__file__).resolve().parent.parent


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pico4824a",
        description="PicoScope 4824A: 8-channel synchronous ADC + AWG controller",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture", help="run one block acquisition")
    capture.add_argument(
        "--config", type=Path, default=PROJECT_DIR / "config.example.json"
    )
    capture.add_argument("--simulate", action="store_true", help="do not access hardware")
    capture.add_argument("--serial", help="open a specific PicoScope serial number")
    capture.add_argument("--output", type=Path, help="output path without extension")
    capture.add_argument("--format", choices=("npz", "csv", "both"), default="npz")

    sub.add_parser("gui", help="start desktop control interface")

    web = sub.add_parser("web", help="start local browser control interface")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=4824)

    generate = sub.add_parser("generate-config", help="write a fresh default JSON config")
    generate.add_argument("path", type=Path, nargs="?", default=PROJECT_DIR / "config.json")

    bias = sub.add_parser("bias-scan", help="scan magnetostrictive bias current (0–6 A)")
    bias.add_argument("--config", type=Path, default=PROJECT_DIR / "configs/bias-scan.example.json")
    bias.add_argument("--simulate", action="store_true")
    bias.add_argument("--resource", default="")
    return parser


def _capture(args: argparse.Namespace) -> int:
    config = AcquisitionConfig.load(args.config)
    stem = args.output
    if stem and stem.suffix in {".npz", ".csv"}:
        stem = stem.with_suffix("")
    print(
        f"Opening {'simulator' if args.simulate else 'PicoScope 4824A'}; "
        f"channels={','.join(config.enabled_channels)}, samples/channel={config.total_samples}"
    )
    with Pico4824A(simulate=args.simulate, serial=args.serial) as scope:
        result = scope.capture(config)
    stem = stem or capture_stem(result)
    print(
        f"Capture complete: {result.samples} samples/channel, "
        f"actual rate={result.actual_sample_rate_hz / 1e6:.6g} MS/s"
    )
    if result.overflow_channels:
        print("WARNING: input overflow on channel(s): " + ", ".join(result.overflow_channels))
    if args.format in {"npz", "both"}:
        print(f"Saved {save_npz(result, stem.parent / (stem.name + '.npz'))}")
    if args.format in {"csv", "both"}:
        print(f"Saved {save_csv(result, stem.parent / (stem.name + '.csv'))}")
    return 0


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        if args.command == "capture":
            code = _capture(args)
        elif args.command == "bias-scan":
            import json
            from .bias_scan import BiasScanConfig, BiasScanController, PicoCaptureAdapter, IT6524DController, SimulatedPowerSupply
            raw = json.loads(args.config.read_text(encoding="utf-8-sig"))
            bias = BiasScanConfig.from_dict(raw["bias"])
            bias.validate(live=not args.simulate)
            if not args.simulate:raise RuntimeError("CLI 未接入独立超时断电保护适配器，实机扫描锁定")
            acquisition = AcquisitionConfig.from_dict(raw.get("acquisition", {}))
            power = SimulatedPowerSupply() if args.simulate else IT6524DController(args.resource or raw.get("resource", ""))
            with Pico4824A(simulate=args.simulate) as device:
                job = BiasScanController(bias, acquisition, power, PicoCaptureAdapter(device), PROJECT_DIR / "data/bias_scans", simulate=args.simulate)
                try: result = job.run()
                finally: power.close()
            print(json.dumps({k:result.get(k) for k in ["status","reason","output_state","best_current_a","output_dir"]}, ensure_ascii=False))
            code = 130 if result.get("interrupted") else (0 if result["status"]=="complete" else 2)
        elif args.command == "generate-config":
            config = AcquisitionConfig()
            config.validate()
            print(f"Saved {config.save(args.path)}")
            code = 0
        elif args.command == "gui":
            from .gui import main as gui_main

            gui_main()
            code = 0
        else:
            from .web import serve

            serve(args.host, args.port)
            code = 0
    except (PicoError, ValueError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        code = 2
    raise SystemExit(code)
