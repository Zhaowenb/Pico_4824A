"""OFF confirmation/transport recovery with fake VISA, never physical devices."""
import unittest
from pico4824a.bias_scan import IT6524DController

class Instrument:
    def __init__(self):
        self.timeout=0;self.identified=False;self.commands=[];self.responses=[];self.clear_count=0
    def write(self,command):self.commands.append(command)
    def query(self,command):
        self.commands.append(command)
        if command=='*IDN?':
            if not self.identified and self.timeout<1000:raise TimeoutError('slow identification')
            self.identified=True
            return 'ITECH,IT6524D,SN,FW'
        if command==':OUTPut:STATe?':
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
        self.assertNotIn(':OUTPut:STATe 1',i.commands)
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
                p,i=self.power();i.responses=[response]*50
                with self.assertRaisesRegex(RuntimeError,r'OUTPut:STATe 0'):p.output_off()
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
            self.assertIn('*IDN?',new.commands);self.assertIn(':OUTPut:STATe 0',new.commands)
            self.assertNotIn(':OUTPut:STATe 1',new.commands)
        finally:control.shutdown()

    def test_reopened_wrong_model_never_receives_output_command(self):
        p,old=self.power()
        new=Instrument()
        new.query=lambda command: 'OTHER,WRONG,SN,FW'
        p.close_transport();p.manager_factory=lambda _:Manager(new)
        with self.assertRaisesRegex(RuntimeError,'型号不匹配'):p.connect()
        self.assertNotIn(':OUTPut:STATe 0',new.commands)

    def test_lamp_off_but_stale_one_reply_is_drained_to_identity(self):
        p,i=self.power()
        i.responses=['1','0','0']
        original=i.query
        def query(command):
            if command=='*IDN?':
                i.commands.append(command)
                return '1' # Old output/measurement reply, not the new IDN reply.
            return original(command)
        i.query=query
        queued=iter(['0.5',p.identity])
        i.read=lambda:next(queued)
        p.output_off()
        self.assertEqual(p.output_state,'off')
        self.assertEqual(i.clear_count,1)
        self.assertEqual(sum(d['command']=='*IDN? continuation' for d in p.diagnostics),2)
        self.assertNotIn(':OUTPut:STATe 1',i.commands)

    def test_status_transition_longer_than_old_100ms_budget(self):
        import time
        p,i=self.power();original_query=i.query;original_write=i.write
        ready=[0.0]
        def write(command):
            original_write(command)
            if command==':OUTPut:STATe 0':ready[0]=time.monotonic()+.25
        def query(command):
            if command==':OUTPut:STATe?':
                i.commands.append(command)
                return '1' if time.monotonic()<ready[0] else '0'
            return original_query(command)
        i.write=write;i.query=query
        start=len(i.commands);p.output_off()
        self.assertEqual(p.output_state,'off')
        self.assertLessEqual(i.commands[start:].count(':OUTPut:STATe 0'),2)

    def test_identity_barrier_rejects_foreign_reply(self):
        p,i=self.power();i.responses=['1']
        original=i.query
        i.query=lambda command:'OTHER,DEVICE,SN,FW' if command=='*IDN?' else original(command)
        with self.assertRaisesRegex(RuntimeError,'身份不匹配'):p.output_off()
        self.assertEqual(p.output_state,'unknown')

    def test_exclusive_session_failure_sends_no_device_commands(self):
        i=Instrument()
        def lock(timeout):raise OSError('resource locked')
        i.lock_excl=lock
        p=IT6524DController('USB::TEST',manager_factory=lambda _:Manager(i))
        with self.assertRaisesRegex(RuntimeError,'独占 USB'):p.connect()
        self.assertEqual(i.commands,[])

    def test_exclusive_session_acquired_before_device_commands(self):
        i=Instrument();locked=[]
        def lock(timeout):
            self.assertEqual(i.commands,[]);locked.append(timeout)
        i.lock_excl=lock
        p=IT6524DController('USB::TEST',manager_factory=lambda _:Manager(i))
        p.connect()
        self.assertTrue(p.exclusive_access);self.assertEqual(locked,[2000])

if __name__=='__main__':unittest.main()
