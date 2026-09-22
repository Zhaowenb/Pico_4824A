import json
from dataclasses import asdict
from pathlib import Path
import threading
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

import numpy as np

from pico4824a.config import AcquisitionConfig, AwgConfig
from pico4824a.lcr import LcrConfig
from pico4824a.web import PicoWebHandler, ThreadingHTTPServer, WebControlState


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), PicoWebHandler)
        output_root = Path(self.temporary.name)
        self.server.control = WebControlState(
            sweep_output_root=output_root / "sweeps",
            lcr_output_root=output_root / "lcr",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def get_json(self, path: str):
        with urlopen(self.base + path, timeout=3) as response:
            return json.loads(response.read())

    def post_json(self, path: str, payload: dict):
        request = Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            return json.loads(response.read())

    def test_simulated_capture_through_http_api(self) -> None:
        config = AcquisitionConfig(
            sample_rate_hz=2_000_000,
            pre_trigger_samples=100,
            post_trigger_samples=900,
        ).to_dict()
        config["simulate"] = True
        reply = self.post_json("/api/capture", config)
        self.assertEqual(reply["capture_id"], 1)

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = self.get_json("/api/status")
            if status["state"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(status["state"], "complete")
        result = self.get_json("/api/result")
        self.assertEqual(result["summary"]["samples"], 1000)
        self.assertEqual(tuple(result["channels"]), tuple("ABCDEFGH"))
        filtered = self.post_json(
            "/api/result/display",
            {
                "filter": {
                    "enabled": True,
                    "low_hz": 20_000,
                    "high_hz": 180_000,
                    "transition_hz": 5_000,
                },
                "max_points": 6000,
            },
        )
        self.assertTrue(filtered["summary"]["display_filter"]["enabled"])
        self.assertEqual(tuple(filtered["channels"]), tuple("ABCDEFGH"))
        self.assertEqual(filtered["summary"]["samples"], 1000)

    def test_simulated_lcr_measurement_through_http_api(self) -> None:
        config = AcquisitionConfig(
            sample_rate_hz=5_000_000,
            pre_trigger_samples=100,
            post_trigger_samples=1000,
        ).to_dict()
        config["simulate"] = True
        for name, channel in config["channels"].items():
            channel["enabled"] = name in {"A", "B"}
        lcr = asdict(LcrConfig(repeats=1, interval_s=0.0))
        reply = self.post_json("/api/lcr/start", {"config": config, "lcr": lcr})
        self.assertEqual(reply["task_id"], 1)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = self.get_json("/api/status")
            if status["state"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(status["state"], "complete", status.get("message"))
        result = self.get_json("/api/lcr/result")
        self.assertEqual(len(result["run_rows"]), 1)
        self.assertEqual(len(result["summary_rows"]), 1)
        self.assertAlmostEqual(result["summary_rows"][0]["frequency_hz"], 100_000.0)
        self.assertTrue(Path(result["directory"], "runs.csv").is_file())
        saved_config = json.loads(Path(result["directory"], "lcr_config.json").read_text(encoding="utf-8"))
        enabled = [
            name for name, channel in saved_config["base_config"]["channels"].items()
            if channel["enabled"]
        ]
        self.assertEqual(enabled, ["A", "B"])
        self.assertEqual(saved_config["base_config"]["trigger"]["source"], "A")

    def test_precision_resistor_calibration_through_http_api(self) -> None:
        config = AcquisitionConfig(
            sample_rate_hz=5_000_000,
            pre_trigger_samples=100,
            post_trigger_samples=1000,
        ).to_dict()
        config["simulate"] = True
        for name, channel in config["channels"].items():
            channel["enabled"] = name in {"A", "B"}
        lcr = asdict(LcrConfig(repeats=1, interval_s=0.0))
        reply = self.post_json(
            "/api/lcr/calibrate",
            {
                "config": config,
                "lcr": lcr,
                "standard_resistance_ohm": 1_000.0,
            },
        )
        self.assertEqual(reply["task_id"], 1)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = self.get_json("/api/status")
            if status["state"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(status["state"], "complete", status.get("message"))
        self.assertEqual(status["task_kind"], "lcr_calibration")
        result = self.get_json("/api/lcr/result")
        calibration_path = Path(result["calibration_file"])
        self.assertTrue(calibration_path.is_file())
        calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
        self.assertEqual(
            calibration["schema"], "pico4824a-lcr-resistor-calibration-v1"
        )
        self.assertAlmostEqual(calibration["standard_resistance_ohm"], 1_000.0)
        self.assertEqual(len(calibration["points"]), 1)
        available = self.get_json("/api/lcr/calibrations")["calibrations"]
        self.assertEqual(len(available), 1)
        self.assertEqual(Path(available[0]["path"]), calibration_path)

    def test_web_hardware_session_is_opened_once_and_reused(self) -> None:
        instances = []

        class FakeDevice:
            def __init__(self, simulate=False):
                self.simulate = simulate
                self.open_calls = 0
                self.close_calls = 0
                instances.append(self)

            def open(self):
                self.open_calls += 1
                return self

            def close(self):
                self.close_calls += 1

        with patch("pico4824a.web.Pico4824A", FakeDevice):
            control = WebControlState()
            control.initialize_hardware()
            first, temporary = control._device_for_task(False)
            second, _ = control._device_for_task(False)
            control.shutdown()

        self.assertFalse(temporary)
        self.assertIs(first, second)
        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].open_calls, 1)
        self.assertEqual(instances[0].close_calls, 1)

    def test_awg_preview_uses_server_generated_composite_waveform(self) -> None:
        awg = AwgConfig(
            waveform="hann_ramp_hold",
            frequency_hz=60_000,
            cycles=5,
            buffer_samples=4096,
            ramp_up_cycles=3,
            hold_cycles=8,
            ramp_down_cycles=3,
            hold_level_ratio=1.0,
        )
        reply = self.post_json("/api/awg-preview", asdict(awg))
        self.assertEqual(reply["buffer_samples"], 4096)
        self.assertAlmostEqual(reply["duration_s"], 19 / 60_000)
        self.assertAlmostEqual(max(reply["volts"]), 1.0, places=3)
        self.assertAlmostEqual(reply["volts"][0], 0.0, places=12)

    def test_web_page_exposes_both_tail_control_modes(self) -> None:
        for route in (
            "/",
            "/measure",
            "/sweep",
            "/lcr",
            "/analysis",
            "/file-analysis",
            "/sweep-analysis",
        ):
            with urlopen(self.base + route, timeout=3) as response:
                html = response.read().decode("utf-8")
        self.assertIn('value="hann_cancel"', html)
        self.assertIn('value="hann_ramp_hold"', html)
        self.assertIn('id="awgPreviewCanvas"', html)
        self.assertIn('value="grid"', html)
        self.assertIn('id="sweepBtn"', html)
        self.assertIn('id="runsViewBtn"', html)
        self.assertIn('id="sweepRunCanvas"', html)
        self.assertIn('id="packetWindowScale"', html)
        self.assertIn('id="recommendationMaxDuration"', html)
        self.assertIn('data-nav="measure"', html)
        self.assertIn('data-nav="sweep"', html)
        self.assertIn('data-nav="lcr"', html)
        self.assertIn('data-nav="file-analysis"', html)
        self.assertIn('data-nav="sweep-analysis"', html)
        self.assertIn('id="captureDurationUs"', html)
        self.assertIn('id="triggerPositionPercent"', html)
        self.assertIn('id="measureFilterEnabled"', html)
        self.assertIn('id="measureBandLow"', html)
        self.assertIn('id="measureBandHigh"', html)
        self.assertIn('id="measureTransition"', html)
        self.assertIn('id="sweepStopBtn"', html)
        self.assertIn('id="lcrStartBtn"', html)
        self.assertIn('id="lcrStopBtn"', html)
        self.assertIn('id="lcrVoltageChannel"', html)
        self.assertIn('id="lcrCurrentChannel"', html)
        self.assertNotIn('id="lcrTriggerChannel"', html)
        self.assertIn('id="lcrTriggerSignal"', html)
        self.assertIn("不需要 AWG 监测或第三根触发线", html)
        self.assertIn('id="lcrBodeCanvas"', html)
        self.assertIn('id="lcrNyquistCanvas"', html)
        self.assertIn('id="lcrImpedanceCanvas"', html)
        self.assertIn('id="lcrParallelResistanceCanvas"', html)
        self.assertIn('id="lcrCapacitanceCanvas"', html)
        self.assertIn('id="lcrInductanceCanvas"', html)
        self.assertIn('id="lcrFactorCanvas"', html)
        self.assertIn('id="lcrSnrCanvas"', html)
        self.assertIn('id="lcrFrequencyAxisScale"', html)
        self.assertIn('id="lcrFrequencyUnit"', html)
        self.assertIn('id="lcrResistanceUnit"', html)
        self.assertIn('id="lcrCapacitanceUnit"', html)
        self.assertIn('id="lcrInductanceUnit"', html)
        self.assertIn('滚轮：时间缩放', html)
        self.assertNotIn('id="preSamples"', html)
        self.assertNotIn('id="postSamples"', html)
        self.assertIn('id="analysisWaveCanvas"', html)
        self.assertIn('id="spectrumCanvas"', html)
        self.assertIn('id="spectrumWindows"', html)
        self.assertIn('id="timeFrequencyCanvas"', html)
        self.assertIn('id="archiveHeatmapCanvas"', html)
        self.assertIn('id="modeWaveCanvas"', html)
        self.assertIn('id="sweepReceiverChannels"', html)
        self.assertIn('id="sweepTransmitterChannel"', html)
        self.assertIn('id="modeReferenceChannel"', html)
        self.assertIn('id="modeComparisonChannel"', html)
        self.assertIn('id="modeTemporalWindow"', html)
        self.assertIn('id="archiveReevaluateBtn"', html)
        self.assertIn('id="archiveRunsTableBody"', html)
        self.assertIn("A–H均可作为评价、显示和分析通道", html)
        with urlopen(self.base + "/app.js", timeout=3) as response:
            javascript = response.read().decode("utf-8")
        self.assertIn("channelNames.map((name)", javascript)
        self.assertNotIn("channelNames.slice(0, 7)", javascript)

    def test_simulated_frequency_sweep_through_http_api(self) -> None:
        config = AcquisitionConfig(
            sample_rate_hz=2_000_000,
            pre_trigger_samples=200,
            post_trigger_samples=1800,
        ).to_dict()
        config["simulate"] = True
        sweep = {
            "mode": "frequency",
            "frequency_start_hz": 60_000,
            "frequency_stop_hz": 70_000,
            "frequency_step_hz": 10_000,
            "cycles_start": 5,
            "cycles_stop": 15,
            "cycles_step": 1,
            "repeats": 1,
            "interval_s": 0,
            "metric_band_low_hz": 20_000,
            "metric_band_high_hz": 180_000,
            "direct_search_start_us": 90,
            "direct_search_end_us": 440,
            "reflection_delay_min_us": 420,
            "reflection_delay_max_us": 570,
            "packet_window_scale": 1,
            "tail_guard_after_cycles": 0,
            "tail_guard_before_cycles": 0,
            "noise_start_us": -95,
            "noise_end_us": -20,
            "settling_threshold_ratio": 0.1,
            "settling_hold_cycles": 2,
            "receiver_channels": ["A", "G"],
            "transmitter_channel": "H",
        }
        reply = self.post_json("/api/sweep/start", {"config": config, "sweep": sweep})
        self.assertEqual(reply["task_id"], 1)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = self.get_json("/api/status")
            if status["state"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(status["state"], "complete")
        result = self.get_json("/api/sweep/result")
        self.assertEqual(len(result["run_rows"]), 2)
        self.assertEqual(len(result["summary_rows"]), 2)
        self.assertIn("nominal_duration_us", result["summary_rows"][0])
        self.assertIn("quality_db", result["summary_rows"][0])
        self.assertNotIn("C_tail_to_direct_db", result["summary_rows"][0])
        run = self.get_json("/api/sweep/run?run_index=1")
        self.assertEqual(run["run"]["run_index"], 1)
        self.assertEqual(run["summary"]["samples"], 2000)
        self.assertEqual(tuple(run["channels"]), tuple("ABCDEFGH"))
        reevaluated = self.post_json(
            "/api/analysis/sweep-reevaluate",
            {
                "path": result["directory"],
                "evaluation": {
                    "receiver_channels": list("ABCDEFGH"),
                    "transmitter_channel": "NONE",
                },
            },
        )
        self.assertTrue(reevaluated["reevaluated"])
        self.assertIn("H_tail_to_direct_db", reevaluated["runs"][0])
        preview = self.post_json(
            "/api/analysis/run-preview",
            {"path": reevaluated["runs"][0]["absolute_npz_file"]},
        )
        self.assertEqual(tuple(preview["channels"]), tuple("ABCDEFGH"))

    def test_running_sweep_can_be_stopped_and_keeps_completed_runs(self) -> None:
        config = AcquisitionConfig(
            sample_rate_hz=2_000_000,
            pre_trigger_samples=200,
            post_trigger_samples=1800,
        ).to_dict()
        config["simulate"] = True
        sweep = {
            "mode": "frequency",
            "frequency_start_hz": 70_000,
            "frequency_stop_hz": 70_000,
            "frequency_step_hz": 1_000,
            "cycles_start": 7,
            "cycles_stop": 7,
            "cycles_step": 1,
            "repeats": 100,
            "interval_s": 0.1,
            "receiver_channels": ["A", "G"],
            "transmitter_channel": "H",
        }
        self.post_json("/api/sweep/start", {"config": config, "sweep": sweep})
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = self.get_json("/api/status")
            if status.get("progress", {}).get("run_index", 0) >= 1:
                break
            time.sleep(0.01)
        self.post_json("/api/stop", {})
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = self.get_json("/api/status")
            if status["state"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(status["state"], "stopped")
        result = self.get_json("/api/sweep/result")
        self.assertGreaterEqual(len(result["run_rows"]), 1)
        self.assertLess(len(result["run_rows"]), 100)

    def test_d_receiver_h_trigger_sweep_through_http_api(self) -> None:
        config = AcquisitionConfig(
            sample_rate_hz=2_000_000,
            pre_trigger_samples=200,
            post_trigger_samples=1800,
        ).to_dict()
        for name in config["channels"]:
            config["channels"][name]["enabled"] = name in {"D", "H"}
        config["trigger"]["source"] = "H"
        config["simulate"] = True
        sweep = {
            "mode": "frequency",
            "frequency_start_hz": 70_000,
            "frequency_stop_hz": 70_000,
            "frequency_step_hz": 1_000,
            "cycles_start": 7,
            "cycles_stop": 7,
            "cycles_step": 1,
            "repeats": 1,
            "interval_s": 0,
            "receiver_channels": ["D"],
            "transmitter_channel": "H",
        }
        self.post_json("/api/sweep/start", {"config": config, "sweep": sweep})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = self.get_json("/api/status")
            if status["state"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(status["state"], "complete")
        result = self.get_json("/api/sweep/result")
        self.assertEqual(result["receiver_channels"], ["D"])
        self.assertIn("D_tail_to_direct_db", result["run_rows"][0])

    def test_file_analysis_through_http_api(self) -> None:
        path = Path(self.temporary.name) / "waveform.npz"
        time_s = np.arange(2000, dtype=np.float64) / 2_000_000
        values = np.sin(2 * np.pi * 70_000 * time_s)
        np.savez_compressed(
            path,
            time_s=time_s,
            **{
                f"ch_{name}_v": values * (1.0 - index * 0.03)
                for index, name in enumerate("ABCDEFGH")
            },
        )
        browse = self.post_json("/api/analysis/browse", {"path": str(path.parent)})
        self.assertEqual(browse["files"], [str(path.resolve())])
        result = self.post_json(
            "/api/analysis/process",
            {
                "path": str(path),
                "channels": ["A", "G"],
                "filter": {
                    "enabled": True,
                    "low_hz": 60_000,
                    "high_hz": 90_000,
                    "transition_hz": 5_000,
                },
                "show_raw": True,
                "show_filtered": True,
                "spectrum": {
                    "source": "filtered",
                    "mode": "amplitude",
                    "window_function": "hann",
                    "frequency_min_hz": 20_000,
                    "frequency_max_hz": 150_000,
                },
            },
        )
        self.assertEqual(tuple(result["waveform"]), ("A:raw", "A:filtered", "G:raw", "G:filtered"))
        self.assertEqual(len(result["spectra"]), 1)
        time_frequency = self.post_json(
            "/api/analysis/time-frequency",
            {
                "path": str(path),
                "channel": "H",
                "method": "stft",
                "start_us": 0,
                "end_us": 900,
                "frequency_min_hz": 20_000,
                "frequency_max_hz": 150_000,
                "window_us": 100,
                "overlap_ratio": 0.75,
                "floor_db": -60,
                "filter": {"enabled": False},
            },
        )
        self.assertEqual(time_frequency["method"], "stft")
        self.assertGreater(len(time_frequency["values_db"]), 1)
        modes = self.post_json(
            "/api/analysis/experimental-modes",
            {
                "path": str(path),
                "reference_channel": "D",
                "comparison_channel": "H",
                "direct_start_us": 100,
                "direct_end_us": 200,
                "search_start_us": 250,
                "search_end_us": 600,
                "end_start_us": 650,
                "end_end_us": 800,
                "filter": {"enabled": False},
            },
        )
        self.assertEqual(modes["status"], "experimental_test_only")
        self.assertEqual(modes["reference_channel"], "D")
        self.assertEqual(modes["comparison_channel"], "H")
        self.assertIn("comparison_to_reference_gain", modes["calibration"])
        self.assertIn("temporal_symmetry_db", modes)
        self.assertEqual(
            tuple(modes["window_metrics"]),
            ("direct", "search", "end_reflection"),
        )


if __name__ == "__main__":
    unittest.main()
