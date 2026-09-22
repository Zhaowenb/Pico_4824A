import unittest

import numpy as np

from pico4824a.experimental_modes import experimental_mode_analysis
from pico4824a.time_frequency import time_frequency_map


class TimeFrequencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sample_rate = 2_000_000.0
        self.time_s = np.arange(2000, dtype=np.float64) / self.sample_rate
        self.signal = np.sin(2 * np.pi * 70_000 * self.time_s)

    def test_stft_wpd_and_cwt_maps(self) -> None:
        common = {
            "start_us": 0,
            "end_us": 900,
            "frequency_min_hz": 20_000,
            "frequency_max_hz": 180_000,
            "floor_db": -60,
        }
        stft = time_frequency_map(
            self.time_s,
            self.signal,
            {
                **common,
                "method": "stft",
                "window_us": 100,
                "overlap_ratio": 0.75,
                "fft_samples": 512,
            },
        )
        self.assertEqual(stft["method"], "stft")
        matrix = np.asarray(stft["values_db"])
        peak_frequency = stft["frequency_hz"][int(np.argmax(np.max(matrix, axis=1)))]
        self.assertAlmostEqual(peak_frequency, 70_000, delta=5_000)

        wpd = time_frequency_map(
            self.time_s,
            self.signal,
            {**common, "method": "wpd", "wpd_level": 5, "wavelet": "db4"},
        )
        self.assertEqual(wpd["details"]["bands"], 32)
        self.assertGreater(len(wpd["frequency_hz"]), 1)

        cwt = time_frequency_map(
            self.time_s,
            self.signal,
            {
                **common,
                "method": "cwt",
                "cwt_bins": 32,
                "morlet_omega0": 6,
            },
        )
        self.assertEqual(len(cwt["frequency_hz"]), 32)
        self.assertEqual(len(cwt["values_db"]), 32)

    def test_experimental_common_differential_and_end_match(self) -> None:
        time_us = self.time_s * 1e6

        def packet(center_us: float, amplitude: float) -> np.ndarray:
            local = time_us - center_us
            inside = np.abs(local) <= 45
            envelope = np.zeros_like(local)
            envelope[inside] = 0.5 + 0.5 * np.cos(np.pi * local[inside] / 45)
            return amplitude * np.sin(2 * np.pi * 70_000 * self.time_s) * envelope

        channel_a = packet(200, 1.0) + packet(450, 0.25) + packet(700, 0.7)
        channel_g = np.roll(channel_a / 1.2, 1)
        result = experimental_mode_analysis(
            self.time_s,
            channel_a,
            channel_g,
            {
                "direct_start_us": 150,
                "direct_end_us": 250,
                "search_start_us": 300,
                "search_end_us": 600,
                "end_start_us": 650,
                "end_end_us": 750,
                "maximum_shift_us": 3,
                "minimum_separation_us": 40,
                "candidate_count": 4,
            },
        )
        self.assertEqual(result["status"], "experimental_test_only")
        self.assertEqual(result["reference_channel"], "A")
        self.assertEqual(result["comparison_channel"], "G")
        self.assertIn("comparison_to_reference_gain", result["calibration"])
        self.assertIn("temporal_symmetry_db", result)
        self.assertIn("direct", result["window_metrics"])
        self.assertLess(result["metrics"]["differential_to_common_db"], -20)
        strongest = max(result["candidates"], key=lambda item: item["absolute_correlation"])
        self.assertAlmostEqual(strongest["time_us"], 450, delta=15)


if __name__ == "__main__":
    unittest.main()
