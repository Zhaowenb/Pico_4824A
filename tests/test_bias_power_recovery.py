"""OFF confirmation/transport recovery with fake VISA, never physical devices."""
import unittest
from pico4824a.bias_scan import IT6524DController

class Instrument:
    def __init__(self):
        self.timeout=0;self.commands=[];self.responses=[];self.clear_count=0
    def write(self,command):self.commands.append(command)
    def query(self,command):
        self.commands.append(command)
        if command=='*IDN?':
            if self.timeout<1000:raise TimeoutError('slow identification')
            return 'ITECH,IT6524D,SN,FW'
        if command=='OUTP?':
            value=self.responses.pop(0) if self.responses else '0'
            if isinstance(value,Exception):raise value
            return value
        if command=='MEAS:CURR?':raise TimeoutError('delayed measurement')
        return '0'
    def clear(self):self.clear_count+=1
    def close(self):pass

class Manager:
    def __init__(self,instrument):self.instrument=instrument
    def open_resource(self,resource):return self.instrument
    def close(self):pass

class PowerRecoveryTests(unittest.TestCase):
    def power(self):
        i=Instrument()
        p=IT6524DController('USB::TEST',manager_factory=lambda _:Manager(i))
        p.connect()
        return p,i
    def test_startup_timeout_separate_from_powered_timeout(self):
        p,i=self.power()
        self.assertEqual(i.timeout,200)
        self.assertEqual(p.output_state,'off')
    def test_off_confirmation_waits_for_delayed_state(self):
        p,i=self.power();i.responses=['ON','1','OFF']
        p.output_off()
        self.assertEqual(p.output_state,'off')
        self.assertNotIn('OUTP ON',i.commands)
    def test_timed_out_query_cleared_before_fresh_off_reply(self):
        p,i=self.power();i.responses=[TimeoutError('late reply'),'0']
        p.output_off()
        self.assertEqual(p.output_state,'off');self.assertEqual(i.clear_count,1)
        self.assertFalse(p._needs_clear)
    def test_measurement_timeout_identifies_command_then_off_resyncs(self):
        p,i=self.power();p._off_requested.clear()
        with self.assertRaisesRegex(RuntimeError,r'MEAS:CURR\? 查询失败'):p.read_actual()
        self.assertTrue(p._needs_clear)
        p.output_off();self.assertEqual(i.clear_count,1);self.assertEqual(p.output_state,'off')
    def test_failed_confirmation_keeps_unknown_and_details(self):
        for response in ['ON','2','NaN','not a status']:
            with self.subTest(response=response):
                p,i=self.power();i.responses=[response]*3
                with self.assertRaisesRegex(RuntimeError,r'OUTP OFF / OUTP\?'):p.output_off()
                self.assertEqual(p.output_state,'unknown');self.assertTrue(p.last_error)
    def test_transport_clear_failure_never_confirms_off(self):
        p,i=self.power();p._needs_clear=True
        def fail():raise OSError('USB disconnected')
        i.clear=fail
        with self.assertRaisesRegex(RuntimeError,'USB disconnected'):p.output_off()
        self.assertEqual(p.output_state,'unknown')

    def test_web_recovery_reopens_transport_and_rechecks_model(self):
        from pico4824a.web import WebControlState
        old=Instrument();new=Instrument();instruments=iter([old,new])
        p=IT6524DController('USB::TEST',manager_factory=lambda _:Manager(next(instruments)))
        p.connect()
        old.responses=[TimeoutError('lost USB')]
        old.clear=lambda:(_ for _ in ()).throw(OSError('old handle lost'))
        control=WebControlState();control.bias_power=p;control.bias_unknown=True
        try:
            reply=control.bias_recover()
            self.assertTrue(reply['recovered']);self.assertFalse(control.bias_unknown)
            self.assertIn('*IDN?',new.commands);self.assertIn('OUTP OFF',new.commands)
            self.assertNotIn('OUTP ON',new.commands)
        finally:control.shutdown()

    def test_reopened_wrong_model_never_receives_output_command(self):
        p,old=self.power()
        new=Instrument()
        new.query=lambda command: 'OTHER,WRONG,SN,FW'
        p.close_transport();p.manager_factory=lambda _:Manager(new)
        with self.assertRaisesRegex(RuntimeError,'型号不匹配'):p.connect()
        self.assertNotIn('OUTP OFF',new.commands)

if __name__=='__main__':unittest.main()
