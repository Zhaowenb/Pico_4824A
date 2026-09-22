"""AWG waveform generation and normalization."""

from __future__ import annotations

from pathlib import Path
import numpy as np

from .config import AwgConfig


BURST_WAVEFORMS = frozenset({"hann_burst", "hann_cancel", "hann_ramp_hold", "lcr_tone"})


def _load_custom_csv(path: str, samples: int) -> np.ndarray:
    values = np.loadtxt(Path(path), delimiter=",", ndmin=1)
    if values.ndim > 1:
        values = values[:, -1]
    if values.size < 2:
        raise ValueError("custom waveform CSV must contain at least two values")
    source_x = np.linspace(0.0, 1.0, values.size, endpoint=False)
    target_x = np.linspace(0.0, 1.0, samples, endpoint=False)
    return np.interp(target_x, source_x, values)


def normalized_waveform(config: AwgConfig) -> tuple[np.ndarray, float]:
    """Return normalized AWG values and whole-buffer repetition frequency.

    The returned repetition frequency is the inverse duration of the complete
    arbitrary-waveform buffer. Composite burst modes therefore play once from
    the beginning of the Hann burst through their configured post-burst stage.
    """
    config.validate()
    n = config.buffer_samples
    phase = np.linspace(0.0, 1.0, n, endpoint=False)

    if config.waveform == "sine":
        values = np.sin(2 * np.pi * phase)
        repetition_hz = config.frequency_hz
    elif config.waveform == "square":
        values = np.where(phase < 0.5, 1.0, -1.0)
        repetition_hz = config.frequency_hz
    elif config.waveform == "triangle":
        values = 1.0 - 4.0 * np.abs(phase - 0.5)
        repetition_hz = config.frequency_hz
    elif config.waveform == "hann_burst":
        carrier = np.sin(2 * np.pi * config.cycles * phase)
        window = np.hanning(n)
        values = carrier * window
        repetition_hz = config.frequency_hz / config.cycles
    elif config.waveform == "hann_cancel":
        carrier_frequency = config.frequency_hz
        cancel_frequency = config.cancel_frequency_hz or carrier_frequency
        burst_duration = config.cycles / carrier_frequency
        delay_duration = config.cancel_delay_cycles / carrier_frequency
        cancel_duration = config.cancel_cycles / cancel_frequency
        total_duration = burst_duration + delay_duration + cancel_duration
        time_s = phase * total_duration
        values = np.zeros(n, dtype=np.float64)

        burst_mask = time_s < burst_duration
        burst_t = time_s[burst_mask]
        burst_envelope = np.sin(np.pi * burst_t / burst_duration) ** 2
        values[burst_mask] = (
            np.sin(2 * np.pi * carrier_frequency * burst_t) * burst_envelope
        )

        cancel_start = burst_duration + delay_duration
        cancel_mask = (time_s >= cancel_start) & (time_s < total_duration)
        cancel_t = time_s[cancel_mask] - cancel_start
        cancel_envelope = np.sin(np.pi * cancel_t / cancel_duration) ** 2
        values[cancel_mask] = (
            config.cancel_amplitude_ratio
            * np.sin(
                2 * np.pi * cancel_frequency * cancel_t
                + np.deg2rad(config.cancel_phase_deg)
            )
            * cancel_envelope
        )
        values[0] = 0.0
        values[-1] = 0.0
        repetition_hz = 1.0 / total_duration
    elif config.waveform == "hann_ramp_hold":
        frequency = config.frequency_hz
        burst_duration = config.cycles / frequency
        ramp_up_duration = config.ramp_up_cycles / frequency
        hold_duration = config.hold_cycles / frequency
        ramp_down_duration = config.ramp_down_cycles / frequency
        total_duration = (
            burst_duration + ramp_up_duration + hold_duration + ramp_down_duration
        )
        time_s = phase * total_duration
        values = np.zeros(n, dtype=np.float64)

        burst_mask = time_s < burst_duration
        burst_t = time_s[burst_mask]
        burst_envelope = np.sin(np.pi * burst_t / burst_duration) ** 2
        values[burst_mask] = np.sin(2 * np.pi * frequency * burst_t) * burst_envelope

        ramp_up_start = burst_duration
        hold_start = ramp_up_start + ramp_up_duration
        ramp_down_start = hold_start + hold_duration
        ramp_up_mask = (time_s >= ramp_up_start) & (time_s < hold_start)
        hold_mask = (time_s >= hold_start) & (time_s < ramp_down_start)
        ramp_down_mask = time_s >= ramp_down_start
        values[ramp_up_mask] = config.hold_level_ratio * (
            (time_s[ramp_up_mask] - ramp_up_start) / ramp_up_duration
        )
        values[hold_mask] = config.hold_level_ratio
        values[ramp_down_mask] = config.hold_level_ratio * (
            1.0 - (time_s[ramp_down_mask] - ramp_down_start) / ramp_down_duration
        )
        values[0] = 0.0
        values[-1] = 0.0
        repetition_hz = 1.0 / total_duration
    elif config.waveform == "lcr_tone":
        # Flat-top coherent tone burst for impedance measurements.  The short
        # cosine ramps suppress configuration/turn-off steps while leaving a
        # steady section for complex sine fitting.
        time_cycles = phase * config.cycles
        envelope = np.ones(n, dtype=np.float64)
        ramp = float(config.tone_ramp_cycles)
        if ramp > 0:
            rising = time_cycles < ramp
            falling = time_cycles > config.cycles - ramp
            envelope[rising] = 0.5 - 0.5 * np.cos(
                np.pi * time_cycles[rising] / ramp
            )
            envelope[falling] = 0.5 - 0.5 * np.cos(
                np.pi * (config.cycles - time_cycles[falling]) / ramp
            )
        values = np.sin(2 * np.pi * time_cycles) * envelope
        values[0] = 0.0
        values[-1] = 0.0
        repetition_hz = config.frequency_hz / config.cycles
    else:
        values = _load_custom_csv(config.custom_csv or "", n)
        repetition_hz = config.frequency_hz

    values = np.asarray(values, dtype=np.float64)
    # Preserve exact zero endpoints for a windowed burst. Other waveform
    # types are centered so that AWG offset remains the sole DC control.
    if config.waveform not in BURST_WAVEFORMS:
        values -= (values.max() + values.min()) / 2.0
    peak = float(np.max(np.abs(values)))
    if peak == 0:
        raise ValueError("AWG waveform is constant; at least two levels are required")
    return np.clip(values / peak, -1.0, 1.0), repetition_hz


def to_dac_counts(values: np.ndarray, minimum: int, maximum: int) -> np.ndarray:
    """Scale normalized [-1, 1] values into driver-provided int16 limits."""
    if minimum >= maximum:
        raise ValueError("invalid AWG DAC limits")
    center = (maximum + minimum) / 2.0
    half_span = (maximum - minimum) / 2.0
    return np.rint(center + np.clip(values, -1.0, 1.0) * half_span).astype(np.int16)
