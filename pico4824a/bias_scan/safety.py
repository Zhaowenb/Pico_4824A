from dataclasses import dataclass
import math
import time


@dataclass(frozen=True)
class TemperatureReading:
    celsius: float
    monotonic_time: float


class SafetyProtection:
    """Implement with a real independent cutoff. UI acknowledgements do not arm hardware."""
    def ready(self):
        return False
    def arm(self, maximum_on_s):
        raise RuntimeError('尚未接入独立超时断电保护适配器')
    def trip(self, reason):
        pass
    def disarm(self):
        pass


class SimulationProtection(SafetyProtection):
    def ready(self):
        return True
    def arm(self, maximum_on_s):
        pass


class SoftwareProtection(SafetyProtection):
    """Controller's deadline/telemetry threads only; no independent cutoff exists.

    The controller creates the deadline thread before enabling output. This
    adapter deliberately does not claim protection from process or host failure.
    """
    def ready(self):
        return True

    def arm(self, maximum_on_s):
        if not math.isfinite(maximum_on_s) or maximum_on_s <= 0:
            raise ValueError('仅软件保护仍需明确最大通电时间')


class TemperatureProvider:
    def read(self) -> TemperatureReading:
        raise RuntimeError('未接入温度传感器；请选择固定冷却模式')
