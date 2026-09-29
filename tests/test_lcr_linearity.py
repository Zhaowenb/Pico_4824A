import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np

from pico4824a.config import AcquisitionConfig, ChannelConfig
from pico4824a.device import CaptureResult, Pico4824A
from pico4824a.lcr_linearity import (
    LinearityConfig,
    _saved_lcr_row_is_valid,
    analyze_linearity_directory,
    execute_linearity_test,
    linearity_run_preview,
    load_lcr_folder,
)
import pico4824a.lcr_linearity as linearity_module


class LargeSignalLinearityTests(unittest.TestCase):
    def make_acquisition(self):
        config = AcquisitionConfig(sample_rate_hz=2_000_000, pre_trigger_samples=300, post_trigger_samples=1700)
        for name in config.channels:
            config.channels[name].enabled = name in {"A", "B", "C"}
            config.channels[name].range = "5V" if name in {"A", "B"} else "2V"
        config.trigger.source = "A"
        config.trigger.threshold_v = 0.01
        return config

    def make_linearity(self):
        return LinearityConfig(
            frequency_hz=60_000, cycles=5, ramp_cycles=0.5,
            vpp_start=0.2, vpp_stop=0.4, vpp_step=0.2, direction="both",
            repeats=1, interval_s=0, sample_rate_hz=2_000_000,
            capture_duration_us=1000, trigger_position_percent=15,
            trigger_level_v=0.01, harmonic_order=3, direct_start_us=0,
            direct_end_us=250, noise_start_us=-120, noise_end_us=-20,
            echo_start_us=500, echo_end_us=800, low_current_reference_count=2,
        )

    def test_three_channel_sweep_preserves_raw_and_reanalyzes_in_memory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            acquisition, cfg = self.make_acquisition(), self.make_linearity()
            result = execute_linearity_test(acquisition, cfg, Pico4824A(simulate=True), root,
                                            threading.Event())
            folder = Path(result["directory"])
            self.assertEqual(len(result["run_rows"]), 4)
            self.assertEqual([row["direction"] for row in result["run_rows"]], ["up", "up", "down", "down"])
            self.assertTrue((folder / "linearity_config.json").is_file())
            self.assertTrue((folder / "linearity_runs.csv").is_file())
            self.assertTrue((folder / "linearity_summary.csv").is_file())
            for row in result["run_rows"]:
                raw = folder / row["raw_file"]
                self.assertTrue(raw.is_file())
                preview = linearity_run_preview(folder, row["run_index"])
                self.assertEqual(set(preview["channels"]), {"A", "B", "C"})
                self.assertTrue(preview["time_s"])
                self.assertTrue(preview["current_receiver_aligned_v"])
                self.assertIn("monitor_state", row)
                self.assertIn("suspected_trip", row)
                self.assertEqual(row["monitor_state"], "OK")
                self.assertGreaterEqual(row["monitor_analysis_cycles"], 2)
                self.assertGreater(row["monitor_analysis_end_us"], row["monitor_analysis_start_us"])
            saved_before = (folder / "linearity_runs.csv").read_bytes()
            direct = load_lcr_folder("linearity", folder)
            self.assertEqual(direct["total_runs"], 4)
            recalculated = load_lcr_folder("linearity", folder, True,
                                           {"direct_start_us": 2, "direct_end_us": 240,
                                            "harmonic_order": 3, "low_current_reference_count": 2})
            self.assertTrue(recalculated["reanalysis"])
            self.assertEqual(recalculated["analysis_settings"]["direct_start_us"], 2)
            self.assertEqual(saved_before, (folder / "linearity_runs.csv").read_bytes())

    def test_suspected_trip_wait_handler_controls_linearity_continuation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original_base_run = linearity_module._base_run
            trip_rows = []

            def mark_trip(capture, config, spec):
                row = original_base_run(capture, config, spec)
                row.update({
                    "suspected_trip": True,
                    "monitor_state": "SUSPECT_TRIP",
                    "monitor_message": "Voltage Monitor 连续掉幅（测试）",
                    "monitor_min_ratio": 0.05,
                })
                return row

            with patch.object(linearity_module, "_base_run", side_effect=mark_trip):
                result = execute_linearity_test(
                    self.make_acquisition(), self.make_linearity(), Pico4824A(simulate=True),
                    root, threading.Event(),
                    on_trip=lambda row: trip_rows.append(dict(row)) or True,
                )
            self.assertFalse(result["stopped"])
            self.assertTrue(result["safety_tripped"])
            self.assertEqual(len(result["run_rows"]), 4)
            self.assertEqual(len(trip_rows), 4)
            self.assertTrue(all(row["trip_action"] == "operator_continue" for row in result["run_rows"]))

    def test_distinct_receiver_and_monitor_channels_are_required(self):
        with self.assertRaisesRegex(ValueError, "三个不同通道"):
            LinearityConfig.from_dict({"voltage_channel": "A", "current_channel": "B", "receiver_channel": "A"})

    def test_suspected_trip_without_operator_continue_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            original_base_run = linearity_module._base_run

            def mark_trip(capture, config, spec):
                row = original_base_run(capture, config, spec)
                row.update({"suspected_trip": True, "monitor_state": "SUSPECT_TRIP"})
                return row

            with patch.object(linearity_module, "_base_run", side_effect=mark_trip):
                result = execute_linearity_test(
                    self.make_acquisition(), self.make_linearity(), Pico4824A(simulate=True),
                    Path(temporary), threading.Event(), on_trip=lambda _row: False,
                )
            self.assertTrue(result["stopped"])
            self.assertTrue(result["safety_tripped"])
            self.assertEqual(len(result["run_rows"]), 1)
            self.assertEqual(result["run_rows"][0]["trip_action"], "operator_stop")

    def test_monitor_window_tracks_burst_offset_from_trigger(self):
        sample_rate = 2_000_000.0
        frequency = 60_000.0
        cycles = 5
        ramp_cycles = 1.0
        burst_start_s = -20e-6
        time_s = np.arange(-100e-6, 160e-6, 1.0 / sample_rate)
        phase_cycles = (time_s - burst_start_s) * frequency
        envelope = np.zeros_like(time_s)
        rising = (phase_cycles >= 0) & (phase_cycles < ramp_cycles)
        plateau = (phase_cycles >= ramp_cycles) & (phase_cycles <= cycles - ramp_cycles)
        falling = (phase_cycles > cycles - ramp_cycles) & (phase_cycles <= cycles)
        envelope[rising] = 0.5 - 0.5 * np.cos(np.pi * phase_cycles[rising] / ramp_cycles)
        envelope[plateau] = 1.0
        envelope[falling] = 0.5 - 0.5 * np.cos(
            np.pi * (cycles - phase_cycles[falling]) / ramp_cycles
        )
        phase = 2 * np.pi * frequency * (time_s - burst_start_s)
        receiver_phase = 2 * np.pi * frequency * (time_s - burst_start_s - 25e-6)
        capture = CaptureResult(
            time_s=time_s,
            volts={"A": 0.5 * envelope * np.sin(phase),
                   "B": 0.2 * envelope * np.sin(phase + 0.15),
                   "C": 0.05 * np.sin(receiver_phase)},
            sample_interval_s=1.0 / sample_rate,
            requested_sample_rate_hz=sample_rate,
            actual_sample_rate_hz=sample_rate,
            overflow_channels=(),
            config=self.make_acquisition(),
            simulated=True,
        )
        config = self.make_linearity()
        config.ramp_cycles = ramp_cycles
        config.minimum_snr_db = 0
        config.validate()
        row = linearity_module._base_run(
            capture, config, {"run_index": 1, "frequency_hz": frequency, "awg_vpp": 0.2}
        )
        self.assertTrue(row["valid"], row.get("analysis_message"))
        self.assertEqual(row["monitor_analysis_cycles"], 3)
        self.assertAlmostEqual(
            row["monitor_analysis_start_us"],
            -20e-6 * 1e6 + 1e6 / frequency,
            delta=2.0,
        )
        self.assertAlmostEqual(
            row["monitor_analysis_end_us"],
            -20e-6 * 1e6 + 4e6 / frequency,
            delta=2.0,
        )

        tripped_volts = {name: values.copy() for name, values in capture.volts.items()}
        trip_time_s = burst_start_s + 2.4 / frequency
        for channel in ("A", "B"):
            tripped_volts[channel][time_s >= trip_time_s] = 0
        tripped_capture = CaptureResult(
            time_s=capture.time_s,
            volts=tripped_volts,
            sample_interval_s=capture.sample_interval_s,
            requested_sample_rate_hz=capture.requested_sample_rate_hz,
            actual_sample_rate_hz=capture.actual_sample_rate_hz,
            overflow_channels=(),
            config=capture.config,
            simulated=True,
        )
        trip_status = linearity_module._suspected_trip(tripped_capture, config)
        self.assertTrue(trip_status["suspected_trip"], trip_status)

    def test_harmonic_order_requires_safe_sample_rate(self):
        config = self.make_linearity()
        config.frequency_hz = 1_000_000
        config.sample_rate_hz = 8_000_000
        config.harmonic_order = 5
        with self.assertRaisesRegex(ValueError, "最高谐波至少需要 4 个采样点"):
            config.validate()

    def test_reanalysis_validity_preserves_safety_and_analysis_failures(self):
        self.assertFalse(_saved_lcr_row_is_valid(
            {"reanalysis_state": "ERROR", "safety_state": "OK"}, "big_lcr"
        ))
        self.assertFalse(_saved_lcr_row_is_valid(
            {"reanalysis_state": "OK", "safety_state": "SUSPECT_TRIP"}, "big_lcr"
        ))
        self.assertFalse(_saved_lcr_row_is_valid(
            {"reanalysis_state": "OK", "valid": False}, "small_lcr"
        ))
        self.assertTrue(_saved_lcr_row_is_valid(
            {"reanalysis_state": "OK", "safety_state": "WARN"}, "big_lcr"
        ))

    def test_directory_reanalysis_uses_saved_config_as_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = execute_linearity_test(self.make_acquisition(), self.make_linearity(),
                                            Pico4824A(simulate=True), Path(temporary), threading.Event())
            folder = Path(result["directory"])
            analysis = analyze_linearity_directory(folder)
            self.assertEqual(analysis["total_runs"], 4)
            self.assertEqual(analysis["analysis_settings"]["frequency_hz"], 60_000)


if __name__ == "__main__":
    unittest.main()
