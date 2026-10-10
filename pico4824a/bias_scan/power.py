import math
import sys
import threading
import time
from collections import deque
from .limits import MAX_CURRENT_A


_usb_refresh_lock = threading.Lock()


def refresh_linux_usb_context(backend=''):
    """Renew PyUSB discovery after Docker hotplug, without closing live handles.

    PyUSB 1.3.x caches one libusb context. Container udev events may not reach
    that context, so enumeration keeps the device list from before a replug.
    New handles use a new context; existing Device objects retain their own
    backend and are not reset/disposed. PicoSDK contexts are independent.
    Keep the PyUSB-private compatibility seam confined to this helper.
    """
    if not sys.platform.startswith('linux') or backend not in ('', '@py'):
        return
    try:
        from usb.backend import libusb1
    except ImportError:
        return  # Native VISA does not require PyUSB; VISA reports missing backends.
    with _usb_refresh_lock:
        previous = libusb1.get_backend()
        if previous is None:
            if not backend:
                return  # Auto-selected native VISA may work without libusb.
            raise RuntimeError('Linux USB 后端不可用，请检查 libusb 与 PyUSB 安装')
        factory = getattr(libusb1, '_LibUSB', None)
        if factory is None or not hasattr(previous, 'lib'):
            raise RuntimeError('当前 PyUSB 版本不支持热插拔刷新，请使用已验证的 PyUSB 1.3.1')
        libusb1._lib_object = factory(previous.lib)


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
        self._needs_clear = False
        self.last_error = ''
        self.diagnostics = deque(maxlen=128)
        self.exclusive_access = None

    def _trace(self, command, reply=None, error=None):
        self.diagnostics.append({'time':time.time(),'command':command,
                                 'reply':str(reply)[:256] if reply is not None else None,
                                 'error':str(error)[:256] if error is not None else None})

    def _write(self, command):
        try:
            self.instrument.write(command)
            self._trace(command)
        except Exception as exc:
            self._trace(command,error=exc)
            raise

    @staticmethod
    def resources(backend='', manager=None):
        try:
            refresh_linux_usb_context(backend)
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
        self._model_confirmed = False
        try:
            if self.manager_factory is None:
                refresh_linux_usb_context(self.backend)
                import pyvisa
                self.manager = pyvisa.ResourceManager(self.backend)
            else:
                self.manager = self.manager_factory(self.backend)
            self.instrument = self.manager.open_resource(self.resource)
            # Identification and initial OFF happen outside a powered scan.
            # Allow startup replies more time without weakening powered timeouts.
            self.instrument.timeout = max(2000, self.timeout_ms)
            self.instrument.write_termination = '\n'
            self.instrument.read_termination = '\n'
            lock=getattr(self.instrument,'lock_excl',None)
            if lock:
                try:
                    lock(timeout=2000)
                    self.exclusive_access=True
                except Exception as exc:
                    # Some VISA backends cannot lock. Do not hide a real
                    # resource-busy error or allow two owners of the device.
                    if getattr(exc,'error_code',None)==-1073807257: # VI_ERROR_NSUP_OPER
                        self.exclusive_access=False
                        self._trace('VISA exclusive lock unsupported',error=exc)
                    else:raise RuntimeError('无法独占 USB 电源，请退出其他电源控制程序或旧服务：'+str(exc)) from exc
            if self._needs_clear:self._clear_transport()
            self.identity = self._query(self.instrument, '*IDN?').strip()
            if 'ITECH' not in self.identity.upper() or 'IT6524D' not in [part.strip() for part in self.identity.upper().split(',')]:
                raise RuntimeError(f'设备型号不匹配：{self.identity}')
            self._model_confirmed = True
            self.instrument.write('SYST:REM')
            self.output_off()
            self.instrument.timeout = self.timeout_ms
            self.last_error = ''
            return self.identity
        except BaseException:
            # Never send model-specific commands to an unidentified instrument.
            if self._model_confirmed:
                try: self.output_off()
                except Exception: pass
            self.close_transport()
            raise

    def _query(self, instrument, command):
        try:
            reply=instrument.query(command)
            self._trace(command,reply=reply)
            return reply
        except Exception as exc:
            self._trace(command,error=exc)
            self._needs_clear = True
            self.last_error = f'{command} 查询失败（超时 {instrument.timeout} ms）：{exc}'
            raise RuntimeError(self.last_error) from exc

    def _clear_transport(self):
        # A timed-out query can leave a delayed reply queued. Never mistake
        # that old measurement for the reply to OUTP?. USBTMC clear resyncs I/O.
        try:
            self.instrument.clear()
        except Exception as exc:
            self.last_error = f'USB 通信失步，清理失败；需要重新连接：{exc}'
            raise RuntimeError(self.last_error) from exc
        self._needs_clear = False

    def _resync_off(self, deadline):
        """Drain to a recognizable identity reply before trusting booleans.

        Send one IDN query, read its queued reply without issuing more queries.
        Numeric stale replies are rejected, never used as an OFF acknowledgement.
        """
        self._clear_transport()
        self._write(':OUTPut:STATe 0')
        reply=self._query(self.instrument,'*IDN?')
        for attempt in range(4):
            text=str(reply).strip()
            if text.upper()==self.identity.strip().upper():
                self._needs_clear=False
                return
            if ',' in text:
                raise RuntimeError('关闭同步时设备身份不匹配：'+text)
            if time.monotonic()>=deadline:
                self._needs_clear=True
                raise RuntimeError('关闭状态同步超时：未收到当前设备身份回复')
            if attempt==3:break
            try:
                reply=self.instrument.read()
                self._trace('*IDN? continuation',reply=reply)
            except Exception as exc:
                self._needs_clear=True
                self._trace('*IDN? continuation',error=exc)
                raise RuntimeError('关闭状态同步失败：'+str(exc)) from exc
        self._needs_clear=True
        raise RuntimeError('关闭状态同步失败：未收到当前设备身份回复')

    @staticmethod
    def _output_enabled(response):
        text = str(response).strip().upper()
        if text in {'ON', 'OFF'}:return text == 'ON'
        try:value = float(text)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f'OUTP? 状态回读格式异常：{text!r}') from exc
        if value not in {0, 1}:
            raise RuntimeError(f'OUTP? 状态回读异常：{text!r}')
        return bool(value)

    def _normal(self, action):
        if self._off_requested.is_set():
            raise RuntimeError('关断请求已锁定电源通信')
        with self._lock:
            if self._off_requested.is_set():
                raise RuntimeError('电源关断优先')
            return action(self.instrument)

    def configure(self, voltage, current):
        if not math.isfinite(current) or not 0 <= current <= MAX_CURRENT_A or not math.isfinite(voltage) or not 0 < voltage <= 360:
            raise ValueError('电压或电流限值无效')
        if self.output_state != 'off':
            raise RuntimeError('必须先确认电源关闭')
        self._off_requested.clear()
        def commands(i):
            i.write(f'VOLT {voltage:.12g}'); i.write(f'CURR {current:.12g}')
            actual_v, actual_i = float(self._query(i,'VOLT?')), float(self._query(i,'CURR?'))
            if not math.isfinite(actual_v) or not math.isfinite(actual_i) or abs(actual_v-voltage)>.02 or abs(actual_i-current)>.011:
                raise RuntimeError('电源设置回读不一致')
        self._normal(commands)

    def output_on(self):
        def enable(i):
            i.write(':OUTPut:STATe 1')
            self.output_state = 'on'
        self._normal(enable)

    def read_actual(self):
        def measure(i):
            current=float(self._query(i,'MEAS:CURR?'))
            if self._off_requested.is_set():raise RuntimeError('电源关断优先')
            voltage=float(self._query(i,'MEAS:VOLT?'))
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
        saved_timeout=self.instrument.timeout
        try:
            self.instrument.timeout=min(saved_timeout,self.timeout_ms)
            # First operation is always OFF. Do not repeatedly resend OFF while
            # polling, which can restart a device's pending state transition.
            self._write(':OUTPut:STATe 0')
            deadline=time.monotonic()+1.0
            synced=False;off_count=0;io_failures=0
            if self._needs_clear:
                self._resync_off(deadline);synced=True
            while True:
                try:
                    response = self._query(self.instrument,':OUTPut:STATe?')
                    last_response=str(response).strip()
                    if self._output_enabled(response):
                        off_count=0
                        self.last_error=f'OUTPut:STATe? 仍返回输出开启：{last_response!r}'
                        if not synced:
                            self._resync_off(deadline);synced=True
                    else:
                        off_count+=1
                        if off_count>=2:
                            self.output_state = 'off'
                            self.last_error = ''
                            return
                except Exception as exc:
                    self.last_error = str(exc)
                    io_failures+=1;off_count=0
                    if io_failures>=3:break
                    if (self._needs_clear or not synced) and time.monotonic()<deadline:
                        try:self._resync_off(deadline);synced=True
                        except Exception as resync_exc:self.last_error=str(resync_exc);break
                if time.monotonic()>=deadline:break
                time.sleep(min(.05,max(0,deadline-time.monotonic())))
            # Never equate zero measured current or an extinguished panel lamp
            # with an acknowledged output switch OFF. Query firmware errors.
            diagnostic=''
            if not self._needs_clear:
                try:diagnostic='；SYST:ERR?='+str(self._query(self.instrument,'SYST:ERR?')).strip()
                except Exception as exc:diagnostic='；设备错误查询失败：'+str(exc)
            raise RuntimeError('OUTPut:STATe 0 关闭确认失败：'+self.last_error+diagnostic)
        finally:
            try:self.instrument.timeout=saved_timeout
            finally:self._lock.release()

    def read_status(self):
        response=self._normal(lambda i:self._query(i,':OUTPut:STATe?'))
        return self._output_enabled(response)

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
