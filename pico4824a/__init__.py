"""PicoScope 4824A acquisition and AWG control package."""

from .config import AcquisitionConfig, AwgConfig, ChannelConfig, TriggerConfig
from .device import CaptureResult, Pico4824A

__all__ = [
    "AcquisitionConfig",
    "AwgConfig",
    "CaptureResult",
    "ChannelConfig",
    "Pico4824A",
    "TriggerConfig",
]

__version__ = "0.1.0"

