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

if __name__=='__main__':unittest.main()
