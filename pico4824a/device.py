"""PicoSDK-backed PicoScope 4824A controller with a hardware-free simulator."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import math
import os
from pathlib import Path
import time
from typing import Any

import numpy as np

from .config import (
    CHANNEL_NAMES,
    RANGE_CODES,
    RANGE_VOLTS,
    AcquisitionConfig,
)
from .waveforms import BURST_WAVEFORMS, normalized_waveform, to_dac_counts


TRIGGER_DIRECTIONS = {
    "above": 0,
    "below": 1,
    "rising": 2,
    "falling": 3,
    "either": 4,
}
AWG_TRIGGER_SOURCES = {"none": 0, "scope": 1, "software": 4}
AWG_DC_WAVE_TYPE = 8
# PS4000A_CONDITIONS_INFO is a bit field in current ps4000aApi.h:
# CLEAR=1, ADD=2.  Older picosdk-python-wrappers releases incorrectly built
# this enum with make_enum(), producing 0 and 1, so do not take this value from
# the installed Python wrapper.
TRIGGER_CONDITIONS_CLEAR = 1


class PicoError(RuntimeError):
    """Raised for PicoSDK or device failures."""


@dataclass(slots=True)
class CaptureResult:
    time_s: np.ndarray
    volts: dict[str, np.ndarray]
    sample_interval_s: float
    requested_sample_rate_hz: float
    actual_sample_rate_hz: float
    overflow_channels: tuple[str, ...]
    config: AcquisitionConfig
    simulated: bool = False

    @property
    def samples(self) -> int:
        return int(self.time_s.size)


class Pico4824A:
    """Control one PicoScope 4824A using the official ps4000a driver."""

    def __init__(self, simulate: bool = False, serial: str | None = None) -> None:
        self.simulate = simulate
        self.serial = serial
        self.handle = ctypes.c_int16()
        self._ps: Any = None
        self._assert_pico_ok: Any = None
        self._is_open = False
        self._awg_buffer: Any = None  # Keep ctypes memory alive while driver uses it.
        self._dll_directory_handles: list[Any] = []

    @property
    def is_open(self) -> bool:
        return self._is_open

    def _load_sdk(self) -> None:
        if self._ps is not None:
            return
        self._prepare_windows_driver_path()
        try:
            from picosdk.functions import assert_pico_ok
            from picosdk.ps4000a import ps4000a as ps
        except (ImportError, OSError) as exc:
            raise PicoError(
                "Unable to load PicoSDK Python wrapper/ps4000a driver. Install the "
                "64-bit PicoSDK, then run: pip install -r requirements.txt"
            ) from exc
        self._ps = ps
        self._assert_pico_ok = assert_pico_ok

    def _prepare_windows_driver_path(self) -> None:
        """Locate a PicoScope 7/PicoSDK ps4000a DLL on Windows.

        PicoScope 7 often installs the driver without adding its directory to
        PATH. ``picosdk`` uses the normal DLL search path, so add a verified
        install directory for this process only; the system PATH is untouched.
        """
        if os.name != "nt":
            return
        candidates: list[Path] = []
        for variable in ("PICO_SDK_PATH", "PICOSDK_PATH"):
            if os.environ.get(variable):
                candidates.append(Path(os.environ[variable]))
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        pico_root = program_files / "Pico Technology"
        candidates.extend(
            [
                pico_root / "SDK" / "lib",
                pico_root / "PicoSDK" / "lib",
                pico_root / "PicoScope 7 T&M Stable",
                pico_root / "PicoScope 7 Automotive Stable",
            ]
        )
        if pico_root.is_dir():
            candidates.extend(path.parent for path in pico_root.glob("Pico*/ps4000a.dll"))

        for directory in candidates:
            dll = directory / "ps4000a.dll"
            if not dll.is_file():
                continue
            directory_text = str(directory.resolve())
            path_parts = os.environ.get("PATH", "").split(os.pathsep)
            if directory_text.lower() not in {part.lower() for part in path_parts}:
                os.environ["PATH"] = directory_text + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                self._dll_directory_handles.append(os.add_dll_directory(directory_text))
            return

    def _check(self, status: int, operation: str) -> None:
        try:
            self._assert_pico_ok(status)
        except Exception as exc:
            raise PicoError(f"{operation} failed (PICO_STATUS={status}): {exc}") from exc

    def open(self) -> "Pico4824A":
        if self._is_open:
            return self
        if self.simulate:
            self._is_open = True
            return self
        self._load_sdk()
        serial = self.serial.encode("ascii") if self.serial else None
        status = self._ps.ps4000aOpenUnit(ctypes.byref(self.handle), serial)
        if status in (282, 286):
            power_status = status
            status = self._ps.ps4000aChangePowerSource(self.handle, power_status)
        self._check(status, "open PicoScope 4824A")
        self._is_open = True
        try:
            # Do not leave the generator in an unknown state inherited from a
            # previous process/session.  This is especially important when an
            # external power amplifier is connected.
            self.disable_awg_output()
        except BaseException:
            try:
                self._ps.ps4000aCloseUnit(self.handle)
            finally:
                self._is_open = False
                self._awg_buffer = None
            raise
        return self

    def close(self) -> None:
        if not self._is_open:
            return
        first_error: BaseException | None = None
        if not self.simulate:
            try:
                self.disable_awg_output()
            except BaseException as exc:
                first_error = exc
            try:
                self._check(self._ps.ps4000aStop(self.handle), "stop capture")
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
            try:
                status = self._ps.ps4000aCloseUnit(self.handle)
                self._check(status, "close device")
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        self._awg_buffer = None
        self._is_open = False
        if first_error is not None:
            raise first_error

    def stop(self) -> None:
        if self._is_open and not self.simulate:
            first_error: BaseException | None = None
            try:
                self.disable_awg_output()
            except BaseException as exc:
                first_error = exc
            try:
                self._check(self._ps.ps4000aStop(self.handle), "stop capture")
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
            if first_error is not None:
                raise first_error

    def disable_awg_output(self) -> None:
        """Force the AWG connector to 0 V using a built-in DC waveform.

        ``ps4000aStop`` only stops ADC acquisition; it does not define the AWG
        output state.  Explicitly selecting 0 V DC avoids retaining an old
        arbitrary buffer and gives the downstream amplifier a deterministic
        input before reconfiguration and after a burst.
        """
        if not self._is_open:
            return
        if self.simulate:
            self._awg_buffer = None
            return
        status = self._ps.ps4000aSetSigGenBuiltIn(
            self.handle,
            0,  # offsetVoltage: 0 uV
            0,  # pkToPk: 0 uV
            AWG_DC_WAVE_TYPE,
            ctypes.c_double(1.0),
            ctypes.c_double(1.0),
            ctypes.c_double(0.0),
            ctypes.c_double(0.0),
            0,  # PS4000A_UP
            0,  # PS4000A_ES_OFF
            0,
            0,
            0,  # PS4000A_SIGGEN_RISING
            AWG_TRIGGER_SOURCES["none"],
            0,
        )
        self._check(status, "set AWG output to 0 V")
        self._awg_buffer = None

    def _disable_awg_output_best_effort(self) -> None:
        try:
            self.disable_awg_output()
        except Exception:
            # Preserve the original acquisition error. close()/shutdown() will
            # make another attempt and disconnect the device regardless.
            pass

    def __enter__(self) -> "Pico4824A":
        return self.open()

    def __exit__(self, *_: object) -> None:
        self.close()

    def _configure_channels(self, config: AcquisitionConfig) -> int:
        max_adc = ctypes.c_int16()
        self._check(
            self._ps.ps4000aMaximumValue(self.handle, ctypes.byref(max_adc)),
            "read ADC maximum",
        )
        for index, name in enumerate(CHANNEL_NAMES):
            channel = config.channels[name]
            coupling = 1 if channel.coupling.upper() == "DC" else 0
            status = self._ps.ps4000aSetChannel(
                self.handle,
                index,
                int(channel.enabled),
                coupling,
                RANGE_CODES[channel.range],
                ctypes.c_float(channel.analog_offset_v),
            )
            self._check(status, f"configure channel {name}")
        return int(max_adc.value)

    def _configure_trigger(self, config: AcquisitionConfig, max_adc: int) -> None:
        # A device can retain advanced-trigger/PWQ conditions from a previous
        # application session.  They are independent of the simple trigger and
        # can make RunBlock reject an otherwise valid edge trigger with
        # PICO_TRIGGER_ERROR.  This application never uses those conditions, so
        # explicitly clear them before installing the simple trigger.
        self._check(
            self._ps.ps4000aSetTriggerChannelConditions(
                self.handle,
                None,
                0,
                TRIGGER_CONDITIONS_CLEAR,
            ),
            "clear previous advanced trigger conditions",
        )
        self._check(
            self._ps.ps4000aSetPulseWidthQualifierConditions(
                self.handle,
                None,
                0,
                TRIGGER_CONDITIONS_CLEAR,
            ),
            "clear previous pulse-width trigger conditions",
        )

        trigger = config.trigger
        if trigger.enabled:
            source_index = CHANNEL_NAMES.index(trigger.source)
            range_v = RANGE_VOLTS[config.channels[trigger.source].range]
            threshold_adc = round(trigger.threshold_v / range_v * max_adc)
            threshold_adc = max(-max_adc, min(max_adc, threshold_adc))
        else:
            source_index = 0
            threshold_adc = 0
        status = self._ps.ps4000aSetSimpleTrigger(
            self.handle,
            int(trigger.enabled),
            source_index,
            threshold_adc,
            TRIGGER_DIRECTIONS[trigger.direction],
            trigger.delay_samples,
            trigger.auto_trigger_ms,
        )
        self._check(status, "configure trigger")

    def _verify_trigger_ready(self, config: AcquisitionConfig) -> None:
        """Verify the driver can apply the requested trigger at RunBlock.

        Pico recommends this query after trigger setup and immediately before
        RunBlock.  It turns an opaque status 44 into a useful configuration
        error and also detects a stale pulse-width qualifier.
        """
        trigger_enabled = ctypes.c_int16()
        pulse_width_enabled = ctypes.c_int16()
        self._check(
            self._ps.ps4000aIsTriggerOrPulseWidthQualifierEnabled(
                self.handle,
                ctypes.byref(trigger_enabled),
                ctypes.byref(pulse_width_enabled),
            ),
            "verify trigger state",
        )
        trigger = config.trigger
        if bool(trigger_enabled.value) != bool(trigger.enabled):
            state = "enabled" if trigger.enabled else "disabled"
            raise PicoError(
                f"driver did not accept the requested {state} trigger state "
                f"for channel {trigger.source}"
            )
        if pulse_width_enabled.value:
            raise PicoError(
                "driver still reports a pulse-width qualifier after it was cleared; "
                "close PicoScope 7 and reconnect the device"
            )

    def _configure_awg(self, config: AcquisitionConfig) -> None:
        # Reconfiguration itself can expose an old/undefined output state.
        # Establish a known 0 V state before touching the arbitrary generator.
        self.disable_awg_output()
        awg = config.awg
        if not awg.enabled:
            return

        values, repetition_hz = normalized_waveform(awg)
        min_dac = ctypes.c_int16()
        max_dac = ctypes.c_int16()
        min_size = ctypes.c_uint32()
        max_size = ctypes.c_uint32()
        self._check(
            self._ps.ps4000aSigGenArbitraryMinMaxValues(
                self.handle,
                ctypes.byref(min_dac),
                ctypes.byref(max_dac),
                ctypes.byref(min_size),
                ctypes.byref(max_size),
            ),
            "read AWG limits",
        )
        if not min_size.value <= values.size <= max_size.value:
            raise PicoError(
                f"AWG buffer length {values.size} is outside device limits "
                f"{min_size.value}..{max_size.value}"
            )

        dac = to_dac_counts(values, min_dac.value, max_dac.value)
        self._awg_buffer = (ctypes.c_int16 * dac.size)(*dac.tolist())
        phase = ctypes.c_uint32()
        self._check(
            self._ps.ps4000aSigGenFrequencyToPhase(
                self.handle,
                ctypes.c_double(repetition_hz),
                0,  # PS4000A_SINGLE
                dac.size,
                ctypes.byref(phase),
            ),
            "calculate AWG DDS phase",
        )

        is_burst = awg.waveform in BURST_WAVEFORMS or awg.trigger_source != "none"
        shots = 1 if is_burst else 0
        trigger_source = AWG_TRIGGER_SOURCES[awg.trigger_source]
        status = self._ps.ps4000aSetSigGenArbitrary(
            self.handle,
            int(round(awg.offset_v * 1_000_000)),
            int(round(awg.pk_to_pk_v * 1_000_000)),
            phase.value,
            phase.value,
            0,
            1,
            self._awg_buffer,
            dac.size,
            0,  # PS4000A_UP
            0,  # extra operation off
            0,  # PS4000A_SINGLE
            shots,
            0,
            0,  # rising
            trigger_source,
            0,
        )
        self._check(status, "configure AWG")

    def _timebase_info(self, timebase: int, samples: int) -> tuple[float, int] | None:
        interval_ns = ctypes.c_float()
        max_samples = ctypes.c_int32()
        status = self._ps.ps4000aGetTimebase2(
            self.handle,
            timebase,
            samples,
            ctypes.byref(interval_ns),
            ctypes.byref(max_samples),
            0,
        )
        if status != 0:
            return None
        return float(interval_ns.value), int(max_samples.value)

    def _find_timebase(self, requested_rate_hz: float, samples: int) -> tuple[int, float, int]:
        target_ns = 1e9 / requested_rate_hz
        first_tb = None
        first_info = None
        for timebase in range(64):
            info = self._timebase_info(timebase, samples)
            if info is not None:
                first_tb, first_info = timebase, info
                break
        if first_tb is None or first_info is None:
            raise PicoError("driver did not report a valid timebase")
        if first_info[0] >= target_ns:
            return first_tb, first_info[0], first_info[1]

        low_tb, low_info = first_tb, first_info
        high_tb = max(first_tb + 1, 2)
        while high_tb < 0x7FFFFFFF:
            high_info = self._timebase_info(high_tb, samples)
            if high_info is not None and high_info[0] >= target_ns:
                break
            if high_info is not None:
                low_tb, low_info = high_tb, high_info
            high_tb = min(high_tb * 2, 0x7FFFFFFF)
        else:
            raise PicoError("unable to bracket requested sampling interval")

        while high_tb - low_tb > 1:
            mid = (low_tb + high_tb) // 2
            mid_info = self._timebase_info(mid, samples)
            if mid_info is None or mid_info[0] < target_ns:
                if mid_info is not None:
                    low_tb, low_info = mid, mid_info
                else:
                    low_tb = mid
            else:
                high_tb, high_info = mid, mid_info

        # Choose the supported interval whose sample rate is closest to requested.
        low_error = abs(math.log(low_info[0] / target_ns))
        high_error = abs(math.log(high_info[0] / target_ns))
        chosen_tb, chosen_info = (
            (low_tb, low_info) if low_error <= high_error else (high_tb, high_info)
        )
        return chosen_tb, chosen_info[0], chosen_info[1]

    def capture(self, config: AcquisitionConfig) -> CaptureResult:
        config.validate()
        if not self._is_open:
            self.open()
        if self.simulate:
            return self._simulate_capture(config)

        try:
            result = self._capture_hardware(config)
        except BaseException:
            self._disable_awg_output_best_effort()
            raise
        self.disable_awg_output()
        return result

    def _capture_hardware(self, config: AcquisitionConfig) -> CaptureResult:

        max_adc = self._configure_channels(config)
        # Configure the generator before the ADC trigger.  On the 4824A both
        # live in the same device and a full signal-generator reconfiguration
        # can invalidate trigger state that was written earlier.
        self._configure_awg(config)
        total = config.total_samples
        timebase, interval_ns, driver_max_samples = self._find_timebase(
            config.sample_rate_hz, total
        )
        if total > driver_max_samples:
            raise PicoError(
                f"capture requests {total} samples/channel but driver allows "
                f"{driver_max_samples} for the current channel/memory setup"
            )

        buffers: dict[str, Any] = {}
        for name in config.enabled_channels:
            buffer = (ctypes.c_int16 * total)()
            buffers[name] = buffer
            self._check(
                self._ps.ps4000aSetDataBuffer(
                    self.handle,
                    CHANNEL_NAMES.index(name),
                    ctypes.byref(buffer),
                    total,
                    0,
                    0,
                ),
                f"register channel {name} data buffer",
            )

        # Install and validate the ADC trigger last, immediately before the
        # acquisition is armed, as required by the PicoSDK block-mode flow.
        self._configure_trigger(config, max_adc)
        self._verify_trigger_ready(config)
        trigger = config.trigger
        run_status = self._ps.ps4000aRunBlock(
            self.handle,
            config.pre_trigger_samples,
            config.post_trigger_samples,
            timebase,
            None,
            0,
            None,
            None,
        )
        operation = "start block capture"
        if trigger.enabled:
            operation += (
                f" with CH {trigger.source} {trigger.direction} trigger "
                f"at {trigger.threshold_v:g} V "
                f"(range +/-{RANGE_VOLTS[config.channels[trigger.source].range]:g} V)"
            )
        self._check(run_status, operation)

        # Recommended synchronized workflow: connect AWG output (or amplifier
        # monitor) to the selected trigger channel. The ADC is armed first,
        # then the software-triggered burst is emitted and caught in hardware.
        if config.awg.enabled and config.awg.trigger_source == "software":
            self._check(
                self._ps.ps4000aSigGenSoftwareControl(self.handle, 1),
                "software-trigger AWG",
            )

        deadline = time.monotonic() + config.capture_timeout_s
        ready = ctypes.c_int16(0)
        while not ready.value:
            self._check(
                self._ps.ps4000aIsReady(self.handle, ctypes.byref(ready)),
                "poll capture state",
            )
            if time.monotonic() >= deadline:
                self.stop()
                raise PicoError(
                    "capture timed out; check trigger level/source and AWG loopback connection"
                )
            time.sleep(0.001)

        returned = ctypes.c_int32(total)
        overflow = ctypes.c_int16()
        self._check(
            self._ps.ps4000aGetValues(
                self.handle,
                0,
                ctypes.byref(returned),
                1,
                0,
                0,
                ctypes.byref(overflow),
            ),
            "transfer captured samples",
        )
        count = returned.value
        time_s = (
            np.arange(count, dtype=np.float64) - config.pre_trigger_samples
        ) * interval_ns * 1e-9
        volts: dict[str, np.ndarray] = {}
        for name, buffer in buffers.items():
            channel = config.channels[name]
            counts = np.ctypeslib.as_array(buffer)[:count].astype(np.float64, copy=True)
            volts[name] = (
                counts * RANGE_VOLTS[channel.range] / max_adc - channel.analog_offset_v
            )
        overflow_channels = tuple(
            name for index, name in enumerate(CHANNEL_NAMES) if overflow.value & (1 << index)
        )
        return CaptureResult(
            time_s=time_s,
            volts=volts,
            sample_interval_s=interval_ns * 1e-9,
            requested_sample_rate_hz=config.sample_rate_hz,
            actual_sample_rate_hz=1e9 / interval_ns,
            overflow_channels=overflow_channels,
            config=config,
        )

    def _simulate_capture(self, config: AcquisitionConfig) -> CaptureResult:
        sample_rate = config.sample_rate_hz
        interval = 1.0 / sample_rate
        time_s = (
            np.arange(config.total_samples, dtype=np.float64) - config.pre_trigger_samples
        ) * interval
        rng = np.random.default_rng(4824)
        volts: dict[str, np.ndarray] = {}
        frequency = config.awg.frequency_hz
        burst_duration = config.awg.cycles / frequency
        if config.awg.enabled:
            awg_values, awg_repetition_hz = normalized_waveform(config.awg)
            awg_duration = 1.0 / awg_repetition_hz
            awg_time = np.linspace(0.0, awg_duration, awg_values.size, endpoint=False)
        else:
            awg_values = np.zeros(2, dtype=np.float64)
            awg_time = np.array([0.0, interval], dtype=np.float64)
        for index, name in enumerate(config.enabled_channels):
            delay = index * 4e-6
            local_t = time_s - delay
            amplitude = config.awg.pk_to_pk_v / 2 * math.exp(-index / 3.5)
            direct = amplitude * np.interp(
                local_t,
                awg_time,
                awg_values,
                left=0.0,
                right=0.0,
            )
            echo_t = local_t - 60e-6
            echo_inside = (echo_t >= 0) & (echo_t <= burst_duration * 1.8)
            echo_envelope = np.zeros_like(time_s)
            echo_envelope[echo_inside] = np.sin(
                np.pi * echo_t[echo_inside] / (burst_duration * 1.8)
            ) ** 2
            echo = 0.22 * amplitude * np.sin(2 * np.pi * frequency * echo_t) * echo_envelope
            noise = rng.normal(0.0, max(amplitude * 0.006, 50e-6), time_s.size)
            volts[name] = np.asarray(direct + echo + noise, dtype=np.float64)
        return CaptureResult(
            time_s=time_s,
            volts=volts,
            sample_interval_s=interval,
            requested_sample_rate_hz=sample_rate,
            actual_sample_rate_hz=sample_rate,
            overflow_channels=(),
            config=config,
            simulated=True,
        )
