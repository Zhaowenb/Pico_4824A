import json
import tempfile
import threading
import time
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from pico4824a.bias_scan import BiasScanConfig
from pico4824a.config import AcquisitionConfig
from pico4824a.web import WebControlState,PicoWebHandler,ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
class BiasHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp')
        self.control=WebControlState();self.control.bias_output_root=Path(self.tmp.name)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),PicoWebHandler);self.server.control=self.control
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.acq=AcquisitionConfig(sample_rate_hz=500000,pre_trigger_samples=50,post_trigger_samples=450).to_dict()
        self.config=asdict(BiasScanConfig(repeats=5,interval_s=0,stable_hold_s=.01,poll_s=.01,cooldown_s=0))
    def tearDown(self):self.control.shutdown();self.server.shutdown();self.server.server_close();self.thread.join(2);self.tmp.cleanup()
    def req(self,path,payload=None):
        req=Request(self.base+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'})
        with urlopen(req,timeout=3) as r:return r.status,r.read()
    def test_full_thirteen_five_and_compatibility(self):
        payload={'simulate':True,'config':self.acq,'bias':self.config}
        self.req('/api/bias-scan/start',payload)
        with self.assertRaises(HTTPError) as err:self.req('/api/capture',{**self.acq,'simulate':True})
        self.assertEqual(err.exception.code,409)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            status=json.loads(self.req('/api/status')[1])
            if status['state'] not in {'running','paused'}:break
            time.sleep(.02)
        r=json.loads(self.req('/api/bias-scan/result')[1])
        self.assertEqual(r['status'],'complete',r['reason']);self.assertEqual(len(r['summary']),13);self.assertEqual(len(r['runs']),65)
        p=json.loads(self.req('/api/bias-scan/preview?point=12&repeat=5')[1]);self.assertEqual(p['row']['target_a'],6);self.assertEqual(len(p['time_us']),1000)
        self.assertEqual(p['vpp_basis'],'filtered');self.assertEqual(p['row']['vpp_basis'],'filtered')
        self.assertEqual(len(p['raw_volts']),len(p['volts']))
        from pico4824a.storage import load_npz
        from pico4824a.bias_scan import BiasScanAnalyzer
        import numpy as np
        loaded=load_npz(Path(r['output_dir'])/p['row']['file'])
        np.testing.assert_allclose(p['volts'],BiasScanAnalyzer.evaluation_signal(loaded,self.control.bias_controller.config))
        np.testing.assert_array_equal(p['raw_volts'],loaded.volts['A'])
        self.assertIn(b'vpp_sd_v',self.req('/api/bias-scan/export?name=summary.csv')[1])
        self.assertEqual(self.req('/bias-scan')[0],200)
        self.assertEqual(self.req('/views/bias-scan.js')[0],200)
        self.req('/api/capture',{**self.acq,'simulate':True})
        worker=self.control._worker_thread
        if worker:worker.join(2)
        self.assertEqual(self.control.status.state,'complete')
    def test_live_limits_and_unknown_guard(self):
        with self.assertRaises(HTTPError):self.req('/api/bias-scan/preflight',{'simulate':False,'config':self.acq,'bias':self.config})
        self.control.bias_unknown=True
        for path,raw in [('/api/capture',{**self.acq,'simulate':True}),('/api/bias-scan/start',{'simulate':True,'config':self.acq,'bias':self.config})]:
            with self.assertRaises(HTTPError) as err:self.req(path,raw)
            self.assertEqual(err.exception.code,409)
        with self.assertRaises(HTTPError):self.req('/api/bias-scan/start',{'simulate':'false','config':self.acq,'bias':self.config})
    def test_server_shutdown_off_during_blocked_capture(self):
        from test_bias_scan import FakePico
        from pico4824a.bias_scan import BiasScanController,SimulatedPowerSupply
        adapter=FakePico('blocked');config=BiasScanConfig(start_a=.5,stop_a=.5,repeats=5,interval_s=0,stable_hold_s=.01,poll_s=.01,cooldown_s=0)
        job=BiasScanController(config,AcquisitionConfig.from_dict(self.acq),SimulatedPowerSupply(),adapter,self.tmp.name,simulate=True)
        self.control.bias_controller=job;worker=threading.Thread(target=job.run);self.control._worker_thread=worker;worker.start()
        self.assertTrue(adapter.started.wait(1));self.control.shutdown();self.assertEqual(job.power.output_state,'off');worker.join(2)
        self.assertFalse(worker.is_alive());self.assertEqual(job.result['status'],'stopped')

    def test_manual_recovery_failure_then_success_and_restart(self):
        from test_bias_scan import FakePico,FaultPower
        from pico4824a.bias_scan import BiasScanController
        power=FaultPower('off')
        cfg=BiasScanConfig(start_a=.5,stop_a=.5,repeats=5,interval_s=0,stable_hold_s=.01,poll_s=.01,cooldown_s=0)
        job=BiasScanController(cfg,AcquisitionConfig.from_dict(self.acq),power,FakePico(),self.tmp.name,simulate=True)
        result=job.run()
        self.assertEqual(result['output_state'],'unknown')
        self.assertIn('OFF failed',result['reason'])
        self.control.bias_power=power;self.control.bias_controller=job;self.control.bias_result=result
        self.control.bias_unknown=True;self.control.status.state='error';self.control.status.task_kind='bias_scan'
        with self.assertRaises(HTTPError):self.req('/api/bias-scan/recover',{})
        self.assertTrue(self.control.bias_unknown)
        power.fault=''
        response=json.loads(self.req('/api/bias-scan/recover',{})[1])
        self.assertTrue(response['recovered']);self.assertEqual(response['output_state'],'off')
        status=json.loads(self.req('/api/status')[1])
        self.assertFalse(status['bias_output_unknown']);self.assertEqual(status['state'],'idle')
        self.assertEqual(status['progress']['output_state'],'off')
        self.assertEqual(result['output_state'],'unknown') # Historical failure is not erased.
        self.assertEqual(result['recovery']['output_state'],'off')
        self.req('/api/bias-scan/start',{'simulate':True,'config':self.acq,'bias':{**self.config,'stop_a':0}})
        worker=self.control._worker_thread
        if worker:worker.join(3)
        self.assertEqual(self.control.status.state,'complete')

    def test_recovery_cannot_unlock_running_or_cleanup_threads(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        power=Mock();power.output_state='off'
        self.control.bias_power=power
        self.control.status.state='running'
        with self.assertRaises(HTTPError):self.req('/api/bias-scan/recover',{})
        power.output_off.assert_not_called()
        self.control.status.state='error'
        thread=Mock();thread.is_alive.return_value=True
        self.control.bias_controller=SimpleNamespace(_threads=[thread])
        with self.assertRaises(HTTPError):self.req('/api/bias-scan/recover',{})
        power.output_off.assert_not_called()
        self.control.bias_controller=None;self.control.bias_power=None

    def test_recovery_without_connection_does_not_claim_off(self):
        with self.assertRaises(HTTPError):self.req('/api/bias-scan/recover',{})

    def test_twenty_amp_limits_and_preflight(self):
        limits=json.loads(self.req('/api/bias-scan/limits')[1])
        self.assertEqual(limits['max_current_a'],21.65)
        payload={'simulate':True,'config':self.acq,'bias':{**self.config,'stop_a':20,'actual_current_limit_a':20}}
        response=json.loads(self.req('/api/bias-scan/preflight',payload)[1])
        self.assertEqual(response['points'][-1],20)
        payload['bias']['stop_a']=20.1
        with self.assertRaises(HTTPError):self.req('/api/bias-scan/preflight',payload)

if __name__=='__main__':unittest.main()
