import unittest

import numpy as np

from pico4824a.config import AwgConfig
from pico4824a.waveforms import normalized_waveform, to_dac_counts


class WaveformTests(unittest.TestCase):
    def test_hann_burst_is_bounded_and_has_correct_repetition(self) -> None:
        config = AwgConfig(frequency_hz=200_000, cycles=5, buffer_samples=2048)
        values, repetition = normalized_waveform(config)
        self.assertEqual(values.shape, (2048,))
        self.assertAlmostEqual(float(np.max(np.abs(values))), 1.0)
        self.assertAlmostEqual(repetition, 40_000)
        self.assertLess(abs(values[0]), 1e-12)

    def test_hann_cancel_appends_bounded_smooth_cancellation(self) -> None:
        config = AwgConfig(
            waveform="hann_cancel",
            frequency_hz=60_000,
            cycles=5,
            buffer_samples=8192,
            cancel_amplitude_ratio=0.25,
            cancel_frequency_hz=60_000,
            cancel_cycles=4,
            cancel_phase_deg=180,
            cancel_delay_cycles=0,
        )
        values, repetition = normalized_waveform(config)
        duration = 1 / repetition
        time_s = np.arange(values.size) / values.size * duration
        post_burst = time_s >= config.cycles / config.frequency_hz
        self.assertAlmostEqual(repetition, 60_000 / 9)
        self.assertGreater(float(np.max(np.abs(values[post_burst]))), 0.24)
        self.assertLessEqual(float(np.max(np.abs(values[post_burst]))), 0.251)
        self.assertEqual(float(values[0]), 0.0)
        self.assertEqual(float(values[-1]), 0.0)

    def test_hann_ramp_hold_has_linear_up_hold_and_linear_down(self) -> None:
        config = AwgConfig(
            waveform="hann_ramp_hold",
            frequency_hz=60_000,
            cycles=5,
            buffer_samples=8192,
            ramp_up_cycles=3,
            hold_cycles=8,
            ramp_down_cycles=3,
            hold_level_ratio=1.0,
        )
        values, repetition = normalized_waveform(config)
        duration = 1 / repetition
        time_s = np.arange(values.size) / values.size * duration

        def value_at(cycles: float) -> float:
            index = int(np.argmin(np.abs(time_s - cycles / config.frequency_hz)))
            return float(values[index])

        self.assertAlmostEqual(repetition, 60_000 / 19)
        self.assertAlmostEqual(value_at(6.5), 0.5, delta=0.01)
        self.assertAlmostEqual(value_at(12.0), 1.0, delta=0.001)
        self.assertAlmostEqual(value_at(17.5), 0.5, delta=0.01)
        self.assertEqual(float(values[0]), 0.0)
        self.assertEqual(float(values[-1]), 0.0)

    def test_lcr_tone_has_flat_measurement_region_and_soft_edges(self) -> None:
        config = AwgConfig(
            waveform="lcr_tone",
            frequency_hz=100_000,
            cycles=40,
            buffer_samples=8192,
            tone_ramp_cycles=3,
        )
        values, repetition = normalized_waveform(config)
        duration = 1 / repetition
        time_s = np.arange(values.size) / values.size * duration
        middle = (time_s >= 10 / config.frequency_hz) & (time_s <= 30 / config.frequency_hz)

        self.assertAlmostEqual(repetition, 2_500)
        self.assertGreater(float(np.max(values[middle])), 0.999)
        self.assertLess(float(np.max(np.abs(values[:20]))), 0.01)
        self.assertEqual(float(values[0]), 0.0)
        self.assertEqual(float(values[-1]), 0.0)

    def test_dac_conversion_reaches_limits(self) -> None:
        result = to_dac_counts(np.array([-1.0, 0.0, 1.0]), -32768, 32767)
        self.assertEqual(result[0], -32768)
        self.assertEqual(result[-1], 32767)
        self.assertIn(int(result[1]), (-1, 0))


if __name__ == "__main__":
    unittest.main()
