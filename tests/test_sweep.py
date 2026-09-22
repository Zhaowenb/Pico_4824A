import csv
from pathlib import Path
import tempfile
import threading
import unittest

import numpy as np

from pico4824a.config import AcquisitionConfig
from pico4824a.device import CaptureResult, Pico4824A
from pico4824a.sweep import SweepConfig, capture_metrics, execute_sweep


class SweepTests(unittest.TestCase):
    def test_axis_modes_include_stop_and_keep_single_axis_fixed(self) -> None:
        base = AcquisitionConfig()
        base.awg.frequency_hz = 75_000
        base.awg.cycles = 10

        frequency = SweepConfig(
            mode="frequency",
            frequency_start_hz=60_000,
            frequency_stop_hz=85_000,
            frequency_step_hz=10_000,
        )
        self.assertEqual(
            frequency.points(base),
            ((60_000.0, 10), (70_000.0, 10), (80_000.0, 10), (85_000.0, 10)),
        )

        cycles = SweepConfig(mode="cycles", cycles_start=5, cycles_stop=10, cycles_step=2)
        self.assertEqual(cycles.points(base), ((75_000.0, 5), (75_000.0, 7), (75_000.0, 9), (75_000.0, 10)))

        grid = SweepConfig(
            mode="grid",
            frequency_start_hz=60_000,
            frequency_stop_hz=70_000,
            frequency_step_hz=10_000,
            cycles_start=5,
            cycles_stop=6,
            cycles_step=1,
            repeats=3,
        )
        self.assertEqual(grid.point_count, 4)
        self.assertEqual(grid.total_runs, 12)

    def test_capture_metrics_separates_tail_and_reflection_windows(self) -> None:
        sample_rate = 2_000_000.0
        time_s = np.arange(-200, 1800, dtype=np.float64) / sample_rate
        time_us = time_s * 1e6
        frequency_hz = 100_000.0
        duration_us = 50.0

        def burst(center_us: float, amplitude: float) -> np.ndarray:
            local = time_us - center_us
            inside = np.abs(local) <= duration_us / 2
            envelope = np.zeros_like(time_us)
            envelope[inside] = 0.5 * (1 + np.cos(2 * np.pi * local[inside] / duration_us))
            return amplitude * np.sin(2 * np.pi * frequency_hz * time_s) * envelope

        direct = burst(200.0, 1.0)
        tail_mask = (time_us >= 225) & (time_us < 675)
        tail = 0.1 * np.sin(2 * np.pi * frequency_hz * time_s) * tail_mask
        reflection = burst(700.0, 0.5)
        receiver = direct + tail + reflection
        base = AcquisitionConfig(sample_rate_hz=sample_rate, pre_trigger_samples=200, post_trigger_samples=1800)
        base.awg.frequency_hz = frequency_hz
        base.awg.cycles = 5
        result = CaptureResult(
            time_s=time_s,
            volts={"A": receiver, "C": receiver * 100, "G": receiver, "H": direct},
            sample_interval_s=1 / sample_rate,
            requested_sample_rate_hz=sample_rate,
            actual_sample_rate_hz=sample_rate,
            overflow_channels=(),
            config=base,
            simulated=True,
        )
        metrics = capture_metrics(result, SweepConfig())
        self.assertAlmostEqual(metrics["nominal_duration_us"], 50.0, places=8)
        self.assertAlmostEqual(metrics["direct_peak_us"], 200.0, delta=2.0)
        self.assertAlmostEqual(metrics["reflection_peak_us"], 700.0, delta=2.0)
        self.assertLess(metrics["tail_to_direct_db"], -10.0)
        self.assertAlmostEqual(metrics["reflection_to_direct_db"], 20 * np.log10(0.5), delta=1.5)
        self.assertIn("C_direct_rms_v", metrics)
        self.assertAlmostEqual(metrics["A_tail_to_direct_db"], metrics["G_tail_to_direct_db"], places=8)

    def test_receiver_channels_are_explicit_and_support_d(self) -> None:
        sweep = SweepConfig.from_dict({"receiver_channels": ["A", "C", "G"]})
        self.assertEqual(sweep.receiver_channels, ("A", "C", "G"))
        d_only = SweepConfig.from_dict({"receiver_channels": ["D"], "transmitter_channel": "H"})
        self.assertEqual(d_only.receiver_channels, ("D",))
        all_eight = SweepConfig.from_dict(
            {"receiver_channels": list("ABCDEFGH"), "transmitter_channel": "NONE"}
        )
        self.assertEqual(all_eight.receiver_channels, tuple("ABCDEFGH"))
        self.assertEqual(all_eight.transmitter_channel, "NONE")

    def test_simulated_sweep_saves_each_npz_and_both_csv_files(self) -> None:
        config = AcquisitionConfig(
            sample_rate_hz=2_000_000,
            pre_trigger_samples=200,
            post_trigger_samples=1800,
        )
        sweep = SweepConfig(
            mode="grid",
            frequency_start_hz=60_000,
            frequency_stop_hz=70_000,
            frequency_step_hz=10_000,
            cycles_start=5,
            cycles_stop=6,
            cycles_step=1,
            repeats=2,
            interval_s=0,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with Pico4824A(simulate=True) as device:
                outcome = execute_sweep(
                    config,
                    sweep,
                    device,
                    Path(temporary),
                    threading.Event(),
                )
            self.assertFalse(outcome.stopped)
            self.assertEqual(len(outcome.run_rows), 8)
            self.assertEqual(len(outcome.summary_rows), 4)
            self.assertEqual(len(list((outcome.directory / "raw").glob("*.npz"))), 8)
            self.assertTrue((outcome.directory / "runs.csv").is_file())
            self.assertTrue((outcome.directory / "summary.csv").is_file())
            with (outcome.directory / "summary.csv").open(encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 4)
            self.assertIn("tail_to_direct_db", rows[0])
            self.assertIn("nominal_duration_us", rows[0])
            self.assertIn("quality_db", rows[0])
            self.assertIn("strength_db_from_max", rows[0])
            self.assertIn("C_tail_to_direct_db", rows[0])

    def test_d_receiver_h_trigger_sweep(self) -> None:
        config = AcquisitionConfig(
            sample_rate_hz=2_000_000,
            pre_trigger_samples=200,
            post_trigger_samples=1800,
        )
        for name, channel in config.channels.items():
            channel.enabled = name in {"D", "H"}
        config.trigger.source = "H"
        config.awg.frequency_hz = 70_000
        config.awg.cycles = 7
        config.validate()
        sweep = SweepConfig(
            mode="frequency",
            frequency_start_hz=70_000,
            frequency_stop_hz=70_000,
            frequency_step_hz=1_000,
            repeats=1,
            interval_s=0,
            receiver_channels=("D",),
            transmitter_channel="H",
        )
        with tempfile.TemporaryDirectory() as temporary:
            with Pico4824A(simulate=True) as device:
                outcome = execute_sweep(
                    config, sweep, device, Path(temporary), threading.Event()
                )
        self.assertEqual(len(outcome.run_rows), 1)
        self.assertEqual(outcome.run_rows[0]["receiver_channels"], "D")
        self.assertIn("D_tail_to_direct_db", outcome.run_rows[0])
        self.assertNotIn("A_tail_to_direct_db", outcome.run_rows[0])


if __name__ == "__main__":
    unittest.main()
