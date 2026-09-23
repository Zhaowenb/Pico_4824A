import math
from pathlib import Path
import tempfile
import threading
import unittest

import numpy as np

from pico4824a.big_signal_lcr import (
    BigSignalLcrConfig,
    _safety_metrics,
    _suggest_monitor_ranges,
    execute_big_signal_lcr,
    monitor_health,
)
from pico4824a.big_signal_linearity import (
    _harmonic_metrics,
    analyze_linearity_directory,
    linearity_run_preview,
)
from pico4824a.config import AcquisitionConfig
from pico4824a.device import CaptureResult, Pico4824A


class BigSignalProtectionAndLinearityTests(unittest.TestCase):
    def test_sustained_midburst_monitor_drop_is_only_suspected_trip(self) -> None:
        frequency = 50_000.0
        sample_rate = 2_000_000.0
        time_s = (np.arange(1400) - 100) / sample_rate
        inside = (time_s >= 0) & (time_s < 30 / frequency)
        amplitude = np.where(time_s >= 16 / frequency, 0.04, 1.0)
        signal = np.where(inside, amplitude * np.sin(2 * np.pi * frequency * time_s), 0.0)
        acquisition = AcquisitionConfig(sample_rate_hz=sample_rate)
        capture = CaptureResult(time_s, {"A": signal, "B": signal * 0.5}, 1 / sample_rate,
                                sample_rate, sample_rate, (), acquisition, True)
        config = BigSignalLcrConfig(frequency_hz=frequency, burst_cycles=30,
                                    ramp_cycles=2, analysis_cycles=10)
        health = monitor_health(capture, config, frequency)
        self.assertEqual(health["monitor_state"], "SUSPECT_TRIP")
        self.assertIn("疑似", health["monitor_message"])

    def test_monitor_range_reduces_only_after_valid_capture(self) -> None:
        acquisition = AcquisitionConfig(sample_rate_hz=2_000_000)
        acquisition.channels["A"].range = "2V"
        acquisition.channels["B"].range = "2V"
        signal = np.sin(np.linspace(0, 20 * np.pi, 500)) * 0.05
        capture = CaptureResult(np.arange(500) / 2_000_000, {"A": signal, "B": signal},
                                0.5e-6, 2_000_000, 2_000_000, (), acquisition, True)
        changes, error = _suggest_monitor_ranges(capture, acquisition, BigSignalLcrConfig())
        self.assertIsNone(error)
        self.assertEqual(changes, {"A": "200mV", "B": "100mV"})

    def test_suspected_trip_stops_remaining_scan_points_and_keeps_raw(self) -> None:
        class CollapsingDevice:
            calls = 0

            def capture(self, acquisition: AcquisitionConfig) -> CaptureResult:
                self.calls += 1
                rate = acquisition.sample_rate_hz
                time_s = (np.arange(acquisition.total_samples) - acquisition.pre_trigger_samples) / rate
                frequency = acquisition.awg.frequency_hz
                inside = (time_s >= 0) & (time_s < acquisition.awg.cycles / frequency)
                gain = np.where(time_s >= 17 / frequency, 0.03, 1.0)
                signal = np.where(inside, gain * np.sin(2 * np.pi * frequency * time_s), 0.0)
                return CaptureResult(time_s, {"A": signal, "B": signal * 0.1}, 1 / rate,
                                     rate, rate, (), acquisition, True)

        acquisition = AcquisitionConfig(sample_rate_hz=2_000_000)
        for name in acquisition.channels:
            acquisition.channels[name].enabled = name in {"A", "B"}
        config = BigSignalLcrConfig(
            scan_mode="voltage", frequency_hz=50_000, awg_vpp_start=0.5,
            awg_vpp_stop=1, awg_vpp_step=0.5, repeats=1, interval_s=0,
            burst_cycles=30, ramp_cycles=2, analysis_cycles=10,
        )
        device = CollapsingDevice()
        with tempfile.TemporaryDirectory() as temporary:
            outcome = execute_big_signal_lcr(acquisition, config, device,
                                             Path(temporary), threading.Event())
            self.assertTrue(outcome.safety_tripped)
            self.assertEqual(device.calls, 1)
            self.assertEqual(outcome.run_rows[0]["safety_state"], "SUSPECT_TRIP")
            self.assertEqual(outcome.summary_rows[0]["valid_repeats"], 0)
            self.assertTrue((outcome.directory / outcome.run_rows[0]["npz_file"]).is_file())

    def test_no_monitor_signal_is_not_reexcited_by_auto_range(self) -> None:
        class SilentDevice:
            calls = 0

            def capture(self, acquisition: AcquisitionConfig) -> CaptureResult:
                self.calls += 1
                rate = acquisition.sample_rate_hz
                time_s = (np.arange(acquisition.total_samples) - acquisition.pre_trigger_samples) / rate
                zeros = np.zeros(time_s.size)
                return CaptureResult(time_s, {"A": zeros, "B": zeros}, 1 / rate,
                                     rate, rate, (), acquisition, True)

        acquisition = AcquisitionConfig(sample_rate_hz=2_000_000)
        for name in acquisition.channels:
            acquisition.channels[name].enabled = name in {"A", "B"}
        device = SilentDevice()
        config = BigSignalLcrConfig(frequency_hz=100_000, burst_cycles=20, ramp_cycles=2,
                                    analysis_cycles=8, auto_monitor_range=True)
        with tempfile.TemporaryDirectory() as temporary:
            outcome = execute_big_signal_lcr(acquisition, config, device,
                                             Path(temporary), threading.Event())
        self.assertEqual(device.calls, 1)
        self.assertTrue(outcome.safety_tripped)
        self.assertEqual(outcome.run_rows[0]["safety_state"], "MONITOR_FAULT")

    def test_user_peak_alert_warns_and_continues_scan(self) -> None:
        class HighCurrentDevice:
            calls = 0

            def capture(self, acquisition: AcquisitionConfig) -> CaptureResult:
                self.calls += 1
                rate = acquisition.sample_rate_hz
                time_s = (np.arange(acquisition.total_samples) - acquisition.pre_trigger_samples) / rate
                inside = (time_s >= 0) & (time_s < acquisition.awg.cycles / acquisition.awg.frequency_hz)
                sine = np.where(inside, np.sin(2 * np.pi * acquisition.awg.frequency_hz * time_s), 0.0)
                return CaptureResult(time_s, {"A": sine * 0.1, "B": sine * 2.2},
                                     1 / rate, rate, rate, (), acquisition, True)

        acquisition = AcquisitionConfig(sample_rate_hz=2_000_000)
        for name in acquisition.channels:
            acquisition.channels[name].enabled = name in {"A", "B"}
        device = HighCurrentDevice()
        config = BigSignalLcrConfig(
            scan_mode="voltage", frequency_hz=100_000,
            awg_vpp_start=0.5, awg_vpp_stop=1.0, awg_vpp_step=0.5,
            burst_cycles=20, ramp_cycles=2, analysis_cycles=8,
            auto_monitor_range=True, interval_s=0,
        )
        with tempfile.TemporaryDirectory() as temporary:
            outcome = execute_big_signal_lcr(acquisition, config, device,
                                             Path(temporary), threading.Event())
        self.assertGreater(device.calls, 2)
        self.assertEqual(len(outcome.run_rows), 2)
        self.assertFalse(outcome.stopped)
        self.assertFalse(outcome.safety_tripped)
        self.assertEqual([row["safety_state"] for row in outcome.run_rows], ["WARN", "WARN"])
        self.assertGreater(outcome.run_rows[0]["current_peak_a"], 2.0)
        self.assertIn("2 Apeak", outcome.run_rows[0]["safety_message"])

    def test_voltage_alert_uses_vpp_without_manufacturer_stop(self) -> None:
        config = BigSignalLcrConfig(scan_mode="single", awg_drive_vpp=4,
                                    max_drive_vpp=4, ata_voltage_gain=60)
        config.validate()  # 240 Vpp estimate is a warning, not a hard cap.
        below = _safety_metrics(
            {"frequency_hz": 100_000, "voltage_vpp_v": 250, "current_peak_a": 0.6},
            config, 0.5,
        )
        self.assertEqual(below["safety_state"], "OK")
        self.assertNotIn("超过", below["safety_message"])
        exceeded = _safety_metrics(
            {"frequency_hz": 100_000, "voltage_vpp_v": 420, "current_peak_a": 2.1},
            config, 0.5,
        )
        self.assertEqual(exceeded["safety_state"], "WARN")
        self.assertIn("400 Vpp", exceeded["safety_message"])
        self.assertIn("2 Apeak", exceeded["safety_message"])

    def test_legacy_rms_thresholds_convert_to_vpp_and_peak(self) -> None:
        config = BigSignalLcrConfig.from_dict({
            "max_voltage_rms_v": 20.0, "max_current_rms_a": 0.2,
        })
        self.assertAlmostEqual(config.alert_voltage_vpp_v, 40 * math.sqrt(2))
        self.assertAlmostEqual(config.alert_current_peak_a, 0.2 * math.sqrt(2))

    def test_simulated_scan_saves_auto_range_attempts_and_supports_offline_analysis(self) -> None:
        acquisition = AcquisitionConfig(sample_rate_hz=5_000_000)
        for name in acquisition.channels:
            acquisition.channels[name].enabled = name in {"A", "B"}
        config = BigSignalLcrConfig(
            frequency_hz=100_000, repeats=1, interval_s=0, burst_cycles=20,
            ramp_cycles=2, analysis_cycles=8, analysis_guard_cycles=1,
            auto_monitor_range=True, ata_voltage_gain=1,
        )
        with tempfile.TemporaryDirectory() as temporary, Pico4824A(simulate=True) as device:
            outcome = execute_big_signal_lcr(acquisition, config, device,
                                             Path(temporary), threading.Event())
            self.assertFalse(outcome.safety_tripped)
            self.assertEqual(outcome.run_rows[0]["safety_state"], "OK")
            self.assertGreaterEqual(outcome.run_rows[0]["auto_range_attempts"], 2)
            self.assertEqual(len(list((outcome.directory / "raw").glob("*.npz"))),
                             outcome.run_rows[0]["auto_range_attempts"])
            analysis = analyze_linearity_directory(outcome.directory, 5)
            self.assertEqual(analysis["valid_runs"], 1)
            self.assertEqual(len(analysis["summary_rows"]), 1)
            preview = linearity_run_preview(outcome.directory, 1)
            self.assertEqual(len(preview["time_s"]), len(preview["voltage_v"]))

    def test_simultaneous_harmonic_fit_recovers_thd(self) -> None:
        frequency = 10_000.0
        time_s = np.arange(2000) / 1_000_000.0
        signal = np.sin(2 * np.pi * frequency * time_s) + 0.1 * np.sin(4 * np.pi * frequency * time_s)
        metrics = _harmonic_metrics(time_s, signal, frequency, 5)
        self.assertAlmostEqual(metrics["thd_pct"], 10.0, places=7)
        self.assertAlmostEqual(metrics["h2_dbc"], -20.0, places=7)
        self.assertLess(metrics["thdn_estimate_pct"] - 10.0, 1e-5)


if __name__ == "__main__":
    unittest.main()
