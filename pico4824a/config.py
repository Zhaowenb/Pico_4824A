"""Validated configuration objects for acquisition and excitation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any


CHANNEL_NAMES = tuple("ABCDEFGH")

# PicoScope 4824A one-times probe input ranges. Values are full-scale volts.
RANGE_VOLTS: dict[str, float] = {
    "10mV": 0.010,
    "20mV": 0.020,
    "50mV": 0.050,
    "100mV": 0.100,
    "200mV": 0.200,
    "500mV": 0.500,
    "1V": 1.0,
    "2V": 2.0,
    "5V": 5.0,
    "10V": 10.0,
    "20V": 20.0,
    "50V": 50.0,
}
RANGE_CODES = {name: code for code, name in enumerate(RANGE_VOLTS)}


@dataclass(slots=True)
class ChannelConfig:
    enabled: bool = True
    range: str = "5V"
    coupling: str = "DC"
    analog_offset_v: float = 0.0

    def validate(self) -> None:
        if self.range not in RANGE_VOLTS:
            raise ValueError(f"Unsupported input range {self.range!r}")
        if self.coupling.upper() not in {"AC", "DC"}:
            raise ValueError("coupling must be AC or DC")


@dataclass(slots=True)
class TriggerConfig:
    enabled: bool = True
    source: str = "A"
    threshold_v: float = 0.10
    direction: str = "rising"
    auto_trigger_ms: int = 2000
    delay_samples: int = 0

    def validate(self, channels: dict[str, ChannelConfig]) -> None:
        self.source = self.source.upper()
        self.direction = self.direction.lower()
        if self.source not in CHANNEL_NAMES:
            raise ValueError("trigger source must be A through H")
        if self.enabled and not channels[self.source].enabled:
            raise ValueError(f"trigger source channel {self.source} is disabled")
        if self.direction not in {"above", "below", "rising", "falling", "either"}:
            raise ValueError("invalid trigger direction")
        # PicoSDK declares autoTrigger_ms as int16_t.  Reject larger values
        # instead of allowing ctypes to wrap them to a negative timeout, which
        # can surface later as PICO_TRIGGER_ERROR from RunBlock.
        if not 0 <= self.auto_trigger_ms <= 32_767:
            raise ValueError("trigger auto timeout must be within 0..32767 ms")
        if self.delay_samples < 0:
            raise ValueError("trigger timing values cannot be negative")
        full_scale = RANGE_VOLTS[channels[self.source].range]
        if abs(self.threshold_v) > full_scale:
            raise ValueError(
                f"trigger threshold {self.threshold_v} V exceeds channel {self.source} "
                f"range +/-{full_scale} V"
            )


@dataclass(slots=True)
class AwgConfig:
    enabled: bool = True
    waveform: str = "hann_burst"
    frequency_hz: float = 100_000.0
    cycles: int = 5
    pk_to_pk_v: float = 2.0
    offset_v: float = 0.0
    buffer_samples: int = 2048
    trigger_source: str = "software"
    custom_csv: str | None = None
    cancel_amplitude_ratio: float = 0.25
    cancel_frequency_hz: float | None = None
    cancel_cycles: int = 4
    cancel_phase_deg: float = 180.0
    cancel_delay_cycles: float = 0.0
    ramp_up_cycles: float = 3.0
    hold_cycles: float = 8.0
    ramp_down_cycles: float = 3.0
    hold_level_ratio: float = 1.0
    tone_ramp_cycles: float = 2.0

    def validate(self) -> None:
        self.waveform = self.waveform.lower()
        self.trigger_source = self.trigger_source.lower()
        if self.waveform not in {
            "sine",
            "square",
            "triangle",
            "hann_burst",
            "hann_cancel",
            "hann_ramp_hold",
            "lcr_tone",
            "custom",
        }:
            raise ValueError("invalid AWG waveform")
        if self.frequency_hz <= 0 or self.frequency_hz > 1_000_000:
            raise ValueError("AWG frequency must be in (0, 1 MHz]")
        if self.cycles < 1:
            raise ValueError("AWG cycles must be at least 1")
        if not 10 <= self.buffer_samples <= 16_384:
            raise ValueError("AWG buffer_samples must be 10 to 16384")
        if self.pk_to_pk_v <= 0:
            raise ValueError("AWG pk_to_pk_v must be positive")
        if abs(self.offset_v) + self.pk_to_pk_v / 2 > 2.0:
            raise ValueError("AWG offset +/- half-amplitude must remain within +/-2 V")
        if self.trigger_source not in {"none", "scope", "software"}:
            raise ValueError("AWG trigger_source must be none, scope, or software")
        if self.waveform == "custom" and not self.custom_csv:
            raise ValueError("custom AWG waveform requires custom_csv")
        if not 0.0 <= self.cancel_amplitude_ratio <= 1.0:
            raise ValueError("AWG cancel_amplitude_ratio must be within [0, 1]")
        if self.cancel_frequency_hz is not None and not 0 < self.cancel_frequency_hz <= 1_000_000:
            raise ValueError("AWG cancel_frequency_hz must be in (0, 1 MHz]")
        if self.cancel_cycles < 1:
            raise ValueError("AWG cancel_cycles must be at least 1")
        if self.cancel_delay_cycles < 0:
            raise ValueError("AWG cancel_delay_cycles cannot be negative")
        if self.ramp_up_cycles <= 0 or self.ramp_down_cycles <= 0:
            raise ValueError("AWG ramp durations must be positive")
        if self.hold_cycles < 0:
            raise ValueError("AWG hold_cycles cannot be negative")
        if not 0.0 <= self.hold_level_ratio <= 1.0:
            raise ValueError("AWG hold_level_ratio must be within [0, 1]")
        if self.waveform == "lcr_tone" and (
            self.tone_ramp_cycles < 0 or self.tone_ramp_cycles * 2 >= self.cycles
        ):
            raise ValueError("LCR tone ramp must be nonnegative and shorter than half the burst")


def _default_channels() -> dict[str, ChannelConfig]:
    return {name: ChannelConfig() for name in CHANNEL_NAMES}


@dataclass(slots=True)
class AcquisitionConfig:
    sample_rate_hz: float = 20_000_000.0
    pre_trigger_samples: int = 20_000
    post_trigger_samples: int = 180_000
    channels: dict[str, ChannelConfig] = field(default_factory=_default_channels)
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    awg: AwgConfig = field(default_factory=AwgConfig)
    capture_timeout_s: float = 10.0

    @property
    def total_samples(self) -> int:
        return self.pre_trigger_samples + self.post_trigger_samples

    @property
    def enabled_channels(self) -> tuple[str, ...]:
        return tuple(name for name in CHANNEL_NAMES if self.channels[name].enabled)

    @property
    def capture_duration_s(self) -> float:
        return self.total_samples / self.sample_rate_hz

    @property
    def trigger_position_ratio(self) -> float:
        return self.pre_trigger_samples / self.total_samples

    def validate(self) -> None:
        if set(self.channels) != set(CHANNEL_NAMES):
            raise ValueError("channels must contain exactly A through H")
        for channel in self.channels.values():
            channel.validate()
        if not self.enabled_channels:
            raise ValueError("at least one channel must be enabled")
        max_rate = 40_000_000.0 if len(self.enabled_channels) >= 5 else 80_000_000.0
        if self.sample_rate_hz <= 0 or self.sample_rate_hz > max_rate:
            raise ValueError(
                f"requested {self.sample_rate_hz:g} S/s exceeds {max_rate:g} S/s "
                f"with {len(self.enabled_channels)} enabled channels"
            )
        if self.pre_trigger_samples < 0 or self.post_trigger_samples <= 0:
            raise ValueError("sample counts must have pre >= 0 and post > 0")
        if self.total_samples > 100_000_000:
            raise ValueError("this application limits one capture to 100 million samples/channel")
        if self.capture_timeout_s <= 0:
            raise ValueError("capture_timeout_s must be positive")
        self.trigger.validate(self.channels)
        self.awg.validate()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return target

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AcquisitionConfig":
        sample_rate_hz = float(raw.get("sample_rate_hz", 20_000_000.0))
        if "capture_duration_us" in raw:
            total_samples = max(
                2,
                round(sample_rate_hz * float(raw["capture_duration_us"]) * 1e-6),
            )
            if "trigger_position_percent" in raw:
                trigger_ratio = float(raw["trigger_position_percent"]) / 100.0
            else:
                trigger_ratio = float(raw.get("trigger_position_ratio", 0.1))
            if not 0 <= trigger_ratio < 1:
                raise ValueError("trigger position must satisfy 0 <= ratio < 1")
            pre_trigger_samples = min(
                total_samples - 1, round(total_samples * trigger_ratio)
            )
            post_trigger_samples = total_samples - pre_trigger_samples
        else:
            pre_trigger_samples = int(raw.get("pre_trigger_samples", 20_000))
            post_trigger_samples = int(raw.get("post_trigger_samples", 180_000))
        channels_raw = raw.get("channels", {})
        channels = {
            name: ChannelConfig(**channels_raw.get(name, {})) for name in CHANNEL_NAMES
        }
        cfg = cls(
            sample_rate_hz=sample_rate_hz,
            pre_trigger_samples=pre_trigger_samples,
            post_trigger_samples=post_trigger_samples,
            channels=channels,
            trigger=TriggerConfig(**raw.get("trigger", {})),
            awg=AwgConfig(**raw.get("awg", {})),
            capture_timeout_s=raw.get("capture_timeout_s", 10.0),
        )
        cfg.validate()
        return cfg

    @classmethod
    def load(cls, path: str | Path) -> "AcquisitionConfig":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
