import math
import threading
import time


class IT6524DController:
    """ITECH IT6500C/D SCPI over an explicitly selected USB VISA resource."""
    def __init__(self, resource, backend='', timeout_ms=200, manager_factory=None):
        if not str(resource).upper().startswith('USB'):
            raise ValueError('请选择 USB VISA 电源资源')
        self.resource, self.backend, self.timeout_ms = resource, backend, timeout_ms
        self.manager_factory = manager_factory
        self.manager = self.instrument = None
        self.identity = None
        self._model_confirmed = False
        self.output_state = 'unknown'
        self._lock = threading.Lock()
        self._off_requested = threading.Event()

    @staticmethod
    def resources(backend='', manager=None):
        try:
            owned = manager is None
            if owned:
                import pyvisa
                manager = pyvisa.ResourceManager(backend)
            try:
                return [r for r in manager.list_resources() if r.upper().startswith('USB')]
            finally:
                # ResourceManager does not implement the context manager protocol.
                # Do not close a manager owned by an already connected controller.
                if owned:
                    manager.close()
        except ImportError as exc:
            raise RuntimeError('未安装 PyVISA；在 TARGET 虚拟环境安装可选 bias 依赖') from exc
        except Exception as exc:
            raise RuntimeError(f'USB VISA 驱动不可用：{exc}；仿真不需要驱动') from exc

    def connect(self):
        if self.instrument is not None:
            return self.identity
        try:
            if self.manager_factory is None:
                import pyvisa
                self.manager = pyvisa.ResourceManager(self.backend)
            else:
                self.manager = self.manager_factory(self.backend)
            self.instrument = self.manager.open_resource(self.resource)
            self.instrument.timeout = self.timeout_ms
            self.instrument.write_termination = '\n'
            self.instrument.read_termination = '\n'
            self.identity = self.instrument.query('*IDN?').strip()
            if 'ITECH' not in self.identity.upper() or 'IT6524D' not in [part.strip() for part in self.identity.upper().split(',')]:
                raise RuntimeError(f'设备型号不匹配：{self.identity}')
            self._model_confirmed = True
            self.instrument.write('SYST:REM')
            self.output_off()
            return self.identity
        except BaseException:
            # Never send model-specific commands to an unidentified instrument.
            if self._model_confirmed:
                try: self.output_off()
                except Exception: pass
            self.close_transport()
            raise

    def _normal(self, action):
        if self._off_requested.is_set():
            raise RuntimeError('关断请求已锁定电源通信')
        with self._lock:
            if self._off_requested.is_set():
                raise RuntimeError('电源关断优先')
            return action(self.instrument)

    def configure(self, voltage, current):
        if not 0 <= current <= 6 or not math.isfinite(voltage) or not 0 < voltage <= 360:
            raise ValueError('电压或电流限值无效')
        if self.output_state != 'off':
            raise RuntimeError('必须先确认电源关闭')
        self._off_requested.clear()
        def commands(i):
            i.write(f'VOLT {voltage:.12g}'); i.write(f'CURR {current:.12g}')
            actual_v, actual_i = float(i.query('VOLT?')), float(i.query('CURR?'))
            if not math.isfinite(actual_v) or not math.isfinite(actual_i) or abs(actual_v-voltage)>.02 or abs(actual_i-current)>.011:
                raise RuntimeError('电源设置回读不一致')
        self._normal(commands)

    def output_on(self):
        def enable(i):
            i.write('OUTP ON')
            self.output_state = 'on'
        self._normal(enable)

    def read_actual(self):
        def measure(i):
            current=float(i.query('MEAS:CURR?'))
            if self._off_requested.is_set():raise RuntimeError('电源关断优先')
            voltage=float(i.query('MEAS:VOLT?'))
            if not math.isfinite(current) or not math.isfinite(voltage):
                raise RuntimeError('电源遥测不是有限值')
            return {'current_a':current,'voltage_v':voltage,'monotonic_time':time.monotonic(),'wall_time':time.time()}
        return self._normal(measure)

    def output_off(self):
        self._off_requested.set()
        self.output_state = 'unknown'
        if self.instrument is None:
            raise RuntimeError('电源未连接，无法确认输出关闭')
        if not self._lock.acquire(timeout=max(.5,self.timeout_ms/1000*3)):
            raise RuntimeError('通信锁超时，输出状态未知')
        try:
            self.instrument.write('OUTP OFF')
            response = self.instrument.query('OUTP?').strip().upper()
            if response != 'OFF' and float(response) != 0:
                raise RuntimeError('输出关闭未被设备确认')
            self.output_state = 'off'
        finally:
            self._lock.release()

    def read_status(self):
        response=self._normal(lambda i:i.query('OUTP?')).strip().upper()
        if response in {'ON','OFF'}:return response=='ON'
        value=float(response)
        if value not in {0,1}:raise RuntimeError('输出状态回读异常')
        return bool(value)

    def read_off_actual(self):
        if self.output_state != 'off':
            raise RuntimeError('输出未确认关闭')
        # Called only outside the powered interval.
        self._off_requested.clear()
        return self.read_actual()

    def close_transport(self):
        for handle in [self.instrument,self.manager]:
            if handle is not None:
                try: handle.close()
                except Exception: pass
        self.instrument = self.manager = None

    def close(self):
        try:
            if self.instrument is not None:self.output_off()
        finally:self.close_transport()


class SimulatedPowerSupply:
    def __init__(self):
        self._off_requested=threading.Event();self.identity='SIMULATED,IT6524D,NO-HARDWARE'; self.output_state='off'; self.current=0.;self.voltage=0.
    def connect(self):return self.identity
    def configure(self, voltage, current):self._off_requested.clear();self.voltage=voltage;self.current=current
    def output_on(self):
        if self._off_requested.is_set():raise RuntimeError('电源关断优先')
        self.output_state='on'
    def output_off(self):self._off_requested.set();self.output_state='off'
    def read_actual(self):
        current=self.current if self.output_state=='on' else 0.
        return {'current_a':current,'voltage_v':min(self.voltage,current*6.4),'monotonic_time':time.monotonic(),'wall_time':time.time()}
    def read_off_actual(self):return self.read_actual()
    def close(self):self.output_off()
