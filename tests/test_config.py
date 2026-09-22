import unittest

from pico4824a.config import AcquisitionConfig


class ConfigTests(unittest.TestCase):
    def test_duration_and_trigger_position_are_converted_to_samples(self) -> None:
        config = AcquisitionConfig.from_dict(
            {
                "sample_rate_hz": 20_000_000,
                "capture_duration_us": 1_000,
                "trigger_position_percent": 25,
            }
        )
        self.assertEqual(config.total_samples, 20_000)
        self.assertEqual(config.pre_trigger_samples, 5_000)
        self.assertEqual(config.post_trigger_samples, 15_000)
        self.assertAlmostEqual(config.capture_duration_s, 0.001)
        self.assertAlmostEqual(config.trigger_position_ratio, 0.25)

    def test_default_is_eight_channel_valid(self) -> None:
        config = AcquisitionConfig()
        config.validate()
        self.assertEqual(config.enabled_channels, tuple("ABCDEFGH"))
        self.assertEqual(config.total_samples, 200_000)

    def test_eight_channels_reject_more_than_40_msps(self) -> None:
        config = AcquisitionConfig(sample_rate_hz=80_000_000)
        with self.assertRaisesRegex(ValueError, "exceeds"):
            config.validate()

    def test_four_channels_accept_80_msps(self) -> None:
        config = AcquisitionConfig(sample_rate_hz=80_000_000)
        for name in "EFGH":
            config.channels[name].enabled = False
        config.validate()
        self.assertEqual(config.enabled_channels, tuple("ABCD"))

    def test_awg_output_range_is_checked(self) -> None:
        config = AcquisitionConfig()
        config.awg.pk_to_pk_v = 4.0
        config.awg.offset_v = 0.1
        with self.assertRaisesRegex(ValueError, "within"):
            config.validate()

    def test_tail_control_parameters_are_checked(self) -> None:
        config = AcquisitionConfig()
        config.awg.waveform = "hann_cancel"
        config.awg.cancel_amplitude_ratio = 1.1
        with self.assertRaisesRegex(ValueError, "cancel_amplitude_ratio"):
            config.validate()

        config.awg.cancel_amplitude_ratio = 0.25
        config.awg.waveform = "hann_ramp_hold"
        config.awg.ramp_down_cycles = 0
        with self.assertRaisesRegex(ValueError, "ramp durations"):
            config.validate()


if __name__ == "__main__":
    unittest.main()
