from dataclasses import dataclass
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


class TemperatureProvider:
    def read(self) -> TemperatureReading:
        raise RuntimeError('未接入温度传感器；请选择固定冷却模式')
