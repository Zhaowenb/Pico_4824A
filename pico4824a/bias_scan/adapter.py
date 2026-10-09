from ..config import AcquisitionConfig


class PicoCaptureAdapter:
    def __init__(self, device):
        self.device=device
    def prepare(self):self.device.open()
    def capture(self, config, remaining_s):
        local=AcquisitionConfig.from_dict(config.to_dict())
        local.capture_timeout_s=min(local.capture_timeout_s,max(.001,remaining_s))
        return self.device.capture(local)
    def stop(self):self.device.stop()
