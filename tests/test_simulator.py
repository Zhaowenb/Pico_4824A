import unittest

import numpy as np

from pico4824a.config import AcquisitionConfig
from pico4824a.device import Pico4824A


class SimulatorTests(unittest.TestCase):
    def test_eight_channel_capture(self) -> None:
        config = AcquisitionConfig(
            sample_rate_hz=5_000_000,
            pre_trigger_samples=1_000,
            post_trigger_samples=4_000,
        )
        with Pico4824A(simulate=True) as scope:
            result = scope.capture(config)
        self.assertTrue(result.simulated)
        self.assertEqual(result.samples, 5_000)
        self.assertEqual(tuple(result.volts), tuple("ABCDEFGH"))
        self.assertAlmostEqual(result.time_s[1] - result.time_s[0], 1 / 5_000_000)
        self.assertTrue(all(values.shape == (5_000,) for values in result.volts.values()))
        self.assertGreater(np.std(result.volts["A"]), np.std(result.volts["H"]))


if __name__ == "__main__":
    unittest.main()
