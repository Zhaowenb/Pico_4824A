import ctypes
import unittest

from pico4824a.config import AcquisitionConfig
from pico4824a.device import Pico4824A


class FakePs4000A:
    def __init__(self) -> None:
        self.buffers = {}
        self.software_triggered = False
        self.calls = []
        self.fail_ready = False
        self.run_status = 0
        self.report_trigger_enabled = 1
        self.report_pwq_enabled = 0

    def ps4000aSetSigGenBuiltIn(self, _handle, offset, pk_to_pk, wave_type, *_args):
        self.calls.append(("zero", offset, pk_to_pk, wave_type))
        return 0

    def ps4000aMaximumValue(self, _handle, value):
        value._obj.value = 32767
        return 0

    def ps4000aSetChannel(self, *_args):
        return 0

    def ps4000aSetSimpleTrigger(self, *_args):
        self.calls.append(("trigger_config",))
        return 0

    def ps4000aSetTriggerChannelConditions(
        self, _handle, conditions, count, condition_info
    ):
        self.calls.append(("trigger_clear", conditions, count, condition_info))
        return 0

    def ps4000aSetPulseWidthQualifierConditions(
        self, _handle, conditions, count, condition_info
    ):
        self.calls.append(("pwq_clear", conditions, count, condition_info))
        return 0

    def ps4000aIsTriggerOrPulseWidthQualifierEnabled(
        self, _handle, trigger_enabled, pulse_width_enabled
    ):
        trigger_enabled._obj.value = self.report_trigger_enabled
        pulse_width_enabled._obj.value = self.report_pwq_enabled
        self.calls.append(("trigger_check",))
        return 0

    def ps4000aSigGenArbitraryMinMaxValues(self, _handle, minimum, maximum, min_size, max_size):
        minimum._obj.value = -32768
        maximum._obj.value = 32767
        min_size._obj.value = 10
        max_size._obj.value = 16384
        return 0

    def ps4000aSigGenFrequencyToPhase(self, _handle, _frequency, _mode, _size, phase):
        phase._obj.value = 123456
        return 0

    def ps4000aSetSigGenArbitrary(self, *_args):
        self.calls.append(("arbitrary",))
        return 0

    def ps4000aGetTimebase2(self, _handle, timebase, _samples, interval, max_samples, _segment):
        if timebase < 2:
            return 1
        interval._obj.value = (timebase - 1) * 25.0
        max_samples._obj.value = 1_000_000
        return 0

    def ps4000aSetDataBuffer(self, _handle, channel, buffer, _length, _segment, _mode):
        self.buffers[channel] = buffer._obj
        return 0

    def ps4000aRunBlock(self, *_args):
        self.calls.append(("run",))
        return self.run_status

    def ps4000aSigGenSoftwareControl(self, _handle, _state):
        self.software_triggered = True
        self.calls.append(("trigger",))
        return 0

    def ps4000aIsReady(self, _handle, ready):
        if self.fail_ready:
            return 1
        ready._obj.value = 1
        return 0

    def ps4000aGetValues(self, _handle, _start, returned, *_args):
        count = returned._obj.value
        for channel, buffer in self.buffers.items():
            for index in range(count):
                buffer[index] = channel * 100 + index
        return 0

    def ps4000aStop(self, _handle):
        self.calls.append(("stop",))
        return 0

    def ps4000aCloseUnit(self, _handle):
        self.calls.append(("close",))
        return 0


class DriverWorkflowTests(unittest.TestCase):
    def test_block_capture_call_sequence_and_conversion(self) -> None:
        fake = FakePs4000A()
        scope = Pico4824A()
        scope._ps = fake
        scope._assert_pico_ok = lambda status: None if status == 0 else (_ for _ in ()).throw(RuntimeError(status))
        scope._is_open = True
        config = AcquisitionConfig(
            sample_rate_hz=20_000_000,
            pre_trigger_samples=10,
            post_trigger_samples=30,
        )
        result = scope.capture(config)
        self.assertTrue(fake.software_triggered)
        self.assertEqual(result.samples, 40)
        self.assertEqual(result.actual_sample_rate_hz, 20_000_000)
        self.assertAlmostEqual(result.volts["B"][0], 100 / 32767 * 5.0)
        self.assertEqual(result.time_s[10], 0.0)
        awg_calls = [call[0] for call in fake.calls if call[0] in {"zero", "arbitrary", "trigger"}]
        self.assertEqual(awg_calls, ["zero", "arbitrary", "trigger", "zero"])
        ordered_calls = [call[0] for call in fake.calls]
        self.assertLess(ordered_calls.index("arbitrary"), ordered_calls.index("trigger_config"))
        self.assertLess(ordered_calls.index("trigger_config"), ordered_calls.index("trigger_check"))
        self.assertLess(ordered_calls.index("trigger_check"), ordered_calls.index("run"))
        trigger_clear = next(call for call in fake.calls if call[0] == "trigger_clear")
        pwq_clear = next(call for call in fake.calls if call[0] == "pwq_clear")
        self.assertEqual(trigger_clear[1:], (None, 0, 1))
        self.assertEqual(pwq_clear[1:], (None, 0, 1))
        for call in (fake.calls[0], fake.calls[-1]):
            self.assertEqual(call, ("zero", 0, 0, 8))

    def test_stop_and_close_zero_awg_before_stopping_adc(self) -> None:
        fake = FakePs4000A()
        scope = Pico4824A()
        scope._ps = fake
        scope._assert_pico_ok = lambda status: None
        scope._is_open = True

        scope.stop()
        scope.close()

        self.assertEqual(
            [call[0] for call in fake.calls],
            ["zero", "stop", "zero", "stop", "close"],
        )

    def test_capture_error_still_returns_awg_to_zero(self) -> None:
        fake = FakePs4000A()
        fake.fail_ready = True
        scope = Pico4824A()
        scope._ps = fake
        scope._assert_pico_ok = lambda status: (
            None
            if status == 0
            else (_ for _ in ()).throw(RuntimeError(status))
        )
        scope._is_open = True

        with self.assertRaises(RuntimeError):
            scope.capture(
                AcquisitionConfig(
                    sample_rate_hz=20_000_000,
                    pre_trigger_samples=10,
                    post_trigger_samples=30,
                )
            )

        awg_calls = [call[0] for call in fake.calls if call[0] in {"zero", "arbitrary", "trigger"}]
        self.assertEqual(awg_calls, ["zero", "arbitrary", "trigger", "zero"])

    def test_rejects_stale_pulse_width_trigger_before_run_block(self) -> None:
        fake = FakePs4000A()
        fake.report_pwq_enabled = 1
        scope = Pico4824A()
        scope._ps = fake
        scope._assert_pico_ok = lambda status: None
        scope._is_open = True

        with self.assertRaisesRegex(RuntimeError, "pulse-width qualifier"):
            scope.capture(
                AcquisitionConfig(
                    sample_rate_hz=20_000_000,
                    pre_trigger_samples=10,
                    post_trigger_samples=30,
                )
            )

        self.assertNotIn("run", [call[0] for call in fake.calls])

    def test_run_block_trigger_error_includes_actual_trigger_settings(self) -> None:
        fake = FakePs4000A()
        fake.run_status = 44
        scope = Pico4824A()
        scope._ps = fake
        scope._assert_pico_ok = lambda status: (
            None
            if status == 0
            else (_ for _ in ()).throw(RuntimeError("PICO_TRIGGER_ERROR"))
        )
        scope._is_open = True

        with self.assertRaisesRegex(
            RuntimeError,
            r"CH A rising trigger at 0.1 V .*PICO_STATUS=44",
        ):
            scope.capture(
                AcquisitionConfig(
                    sample_rate_hz=20_000_000,
                    pre_trigger_samples=10,
                    post_trigger_samples=30,
                )
            )


if __name__ == "__main__":
    unittest.main()
