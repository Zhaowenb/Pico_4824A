import math
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

import numpy as np

from pico4824a.config import AcquisitionConfig
from pico4824a.device import CaptureResult
from pico4824a.lcr import (
    LcrConfig,
    aggregate_lcr_rows,
    analyze_impedance,
    load_lcr_calibration,
    write_resistor_calibration,
)


class LcrAnalysisTests(unittest.TestCase):
    def synthetic_capture(self, impedance: complex) -> tuple[CaptureResult, LcrConfig, float]:
        frequency = 100_000.0
        sample_rate = 5_000_000.0
        samples_per_cycle = sample_rate / frequency
        pre_samples = 250
        burst_samples = round(40 * samples_per_cycle)
        total = pre_samples + burst_samples + 250
        time_s = (np.arange(total) - pre_samples) / sample_rate
        active_time = np.clip(time_s, 0.0, 40 / frequency)
        cycles = active_time * frequency
        envelope = np.zeros(total)
        inside = (time_s >= 0.0) & (time_s < 40 / frequency)
        envelope[inside] = 1.0
        rising = inside & (cycles < 3)
        falling = inside & (cycles > 37)
        envelope[rising] = 0.5 - 0.5 * np.cos(np.pi * cycles[rising] / 3)
        envelope[falling] = 0.5 - 0.5 * np.cos(np.pi * (40 - cycles[falling]) / 3)

        config = LcrConfig(
            frequency_hz=frequency,
            feedback_resistance_ohm=100.0,
            voltage_gain=2.0,
            current_gain=2.0,
            current_polarity=-1,
            burst_cycles=40,
            ramp_cycles=3,
            analysis_cycles=16,
            analysis_guard_cycles=2,
        )
        voltage_phasor = 0.3 * np.exp(1j * 0.27)
        current_phasor = voltage_phasor / impedance
        omega_t = 2 * np.pi * frequency * time_s
        voltage_output = envelope * np.real(config.voltage_gain * voltage_phasor * np.exp(1j * omega_t))
        current_output_phasor = (
            current_phasor * config.current_gain * config.feedback_resistance_ohm
            / config.current_polarity
        )
        current_output = envelope * np.real(current_output_phasor * np.exp(1j * omega_t))
        acquisition = AcquisitionConfig(
            sample_rate_hz=sample_rate,
            pre_trigger_samples=pre_samples,
            post_trigger_samples=total - pre_samples,
        )
        result = CaptureResult(
            time_s=time_s,
            volts={"A": voltage_output, "B": current_output, "H": voltage_output},
            sample_interval_s=1 / sample_rate,
            requested_sample_rate_hz=sample_rate,
            actual_sample_rate_hz=sample_rate,
            overflow_channels=(),
            config=acquisition,
            simulated=True,
        )
        return result, config, frequency

    def test_recovers_complex_impedance_and_equivalent_capacitance(self) -> None:
        expected = complex(120.0, -80.0)
        result, config, frequency = self.synthetic_capture(expected)
        measured = analyze_impedance(result, config, frequency)

        self.assertAlmostEqual(measured["impedance_real_ohm"], expected.real, places=6)
        self.assertAlmostEqual(measured["impedance_imag_ohm"], expected.imag, places=6)
        self.assertAlmostEqual(
            measured["series_capacitance_f"],
            1 / (2 * math.pi * frequency * abs(expected.imag)),
            places=12,
        )
        self.assertGreater(measured["voltage_fit_r2"], 0.999999)
        self.assertGreater(measured["current_fit_r2"], 0.999999)

    def test_applies_complex_fixture_corrections_in_defined_order(self) -> None:
        expected = complex(75.0, 25.0)
        result, config, frequency = self.synthetic_capture(expected)
        config.series_resistance_ohm = 5.0
        config.series_reactance_ohm = -3.0
        measured = analyze_impedance(result, config, frequency)

        self.assertAlmostEqual(measured["impedance_real_ohm"], 70.0, places=6)
        self.assertAlmostEqual(measured["impedance_imag_ohm"], 28.0, places=6)

    def test_frequency_axes_and_repeat_aggregation(self) -> None:
        linear = LcrConfig(
            mode="linear",
            frequency_start_hz=10_000,
            frequency_stop_hz=21_000,
            frequency_step_hz=5_000,
        )
        self.assertEqual(linear.frequencies_hz, (10_000.0, 15_000.0, 20_000.0, 21_000.0))
        rows = [
            {"frequency_hz": 10_000.0, "repeat": 1, "impedance_real_ohm": 9.0},
            {"frequency_hz": 10_000.0, "repeat": 2, "impedance_real_ohm": 11.0},
        ]
        summary = aggregate_lcr_rows(rows)
        self.assertEqual(summary[0]["repeats_completed"], 2)
        self.assertAlmostEqual(summary[0]["impedance_real_ohm"], 10.0)
        self.assertAlmostEqual(summary[0]["impedance_real_ohm_std"], math.sqrt(2))

    def test_trigger_reuses_one_of_the_two_measurement_channels(self) -> None:
        voltage_trigger = LcrConfig(voltage_channel="C", current_channel="G")
        voltage_trigger.validate()
        self.assertEqual(voltage_trigger.trigger_source_channel, "C")

        current_trigger = LcrConfig(
            voltage_channel="C",
            current_channel="G",
            trigger_signal="current",
        )
        current_trigger.validate()
        self.assertEqual(current_trigger.trigger_source_channel, "G")

    def test_precision_resistor_calibration_corrects_real_and_imaginary_parts(self) -> None:
        standard_ohm = 1_000.0
        systematic_factor = 1.08 * np.exp(1j * np.deg2rad(-7.0))
        measured_standard = standard_ohm / systematic_factor
        expected_dut = complex(120.0, -80.0)
        raw_dut = expected_dut / systematic_factor
        result, config, frequency = self.synthetic_capture(raw_dut)
        rows = [{
            "frequency_hz": frequency,
            "uncalibrated_impedance_real_ohm": measured_standard.real,
            "uncalibrated_impedance_imag_ohm": measured_standard.imag,
        }]
        with tempfile.TemporaryDirectory() as temporary:
            calibration_path = write_resistor_calibration(
                Path(temporary) / "calibration.json",
                rows,
                standard_ohm,
                result.config,
                config,
                Path(temporary),
            )
            loaded = load_lcr_calibration(
                calibration_path, result.config, config
            )
            measured = analyze_impedance(
                result, config, frequency, loaded.factor_at(frequency)
            )

        self.assertAlmostEqual(measured["impedance_real_ohm"], expected_dut.real, places=6)
        self.assertAlmostEqual(measured["impedance_imag_ohm"], expected_dut.imag, places=6)
        self.assertTrue(measured["calibration_applied"])

    def test_resistor_calibration_rejects_changed_front_end_settings(self) -> None:
        result, config, frequency = self.synthetic_capture(complex(1_000.0, 20.0))
        rows = [{
            "frequency_hz": frequency,
            "uncalibrated_impedance_real_ohm": 1_000.0,
            "uncalibrated_impedance_imag_ohm": 20.0,
        }]
        with tempfile.TemporaryDirectory() as temporary:
            calibration_path = write_resistor_calibration(
                Path(temporary) / "calibration.json",
                rows,
                1_000.0,
                result.config,
                config,
                Path(temporary),
            )
            changed = LcrConfig(**asdict(config))
            changed.current_gain *= 2
            with self.assertRaisesRegex(ValueError, "current_gain"):
                load_lcr_calibration(calibration_path, result.config, changed)


if __name__ == "__main__":
    unittest.main()
