import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import numpy as np
from pico4824a.config import AcquisitionConfig
from pico4824a.device import Pico4824A
from pico4824a.repeated_capture import RepeatCaptureConfig,run_capture_sequence
from pico4824a.storage import load_npz
from pico4824a.web import WebControlState

ROOT=Path(__file__).resolve().parents[1]
class RepeatedCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp')
        self.root=Path(self.tmp.name)
        self.config=AcquisitionConfig(sample_rate_hz=500000,pre_trigger_samples=50,post_trigger_samples=450)
    def tearDown(self):self.tmp.cleanup()
    def wait(self,state):
        deadline=time.monotonic()+3
        while state.snapshot()['state']=='running' and time.monotonic()<deadline:time.sleep(.005)
        if state._worker_thread:state._worker_thread.join(1)
        return state.snapshot()
    def test_validation(self):
        self.assertFalse(RepeatCaptureConfig.from_dict().enabled)
        self.assertFalse(RepeatCaptureConfig.from_dict({'enabled':False,'count':'bad'}).enabled)
        for raw in [{'enabled':True,'count':0},{'enabled':True,'count':1.5},{'enabled':True,'count':True},{'enabled':True,'count':10001},{'enabled':True,'echo_wait_s':float('nan')},{'enabled':True,'echo_wait_s':-1},{'enabled':True,'echo_wait_s':3601},{'enabled':'yes'}]:
            with self.subTest(raw=raw),self.assertRaises(ValueError):RepeatCaptureConfig.from_dict(raw)
    def test_sequence_five_raw_files_and_wait_after_return(self):
        frames=[];states=[]
        with Pico4824A(simulate=True) as device:
            result=run_capture_sequence(device,self.config,RepeatCaptureConfig(True,5,.02),self.root,
                                        threading.Event(),lambda r,p:frames.append((r,p)),states.append)
        self.assertEqual(result['status'],'complete');self.assertEqual(result['completed'],5)
        self.assertEqual(len(list(self.root.rglob('*.npz'))),5)
        for previous,current in zip(result['runs'],result['runs'][1:]):
            self.assertGreaterEqual(current['capture_start']-previous['capture_end'],.019)
        for index,(frame,stem) in enumerate(frames,1):
            saved=load_npz(stem.with_suffix('.npz'))
            self.assertEqual(saved.capture_info['repeat_measurement']['index'],index)
            np.testing.assert_array_equal(saved.time_s,frame.time_s)
            for c in saved.volts:np.testing.assert_array_equal(saved.volts[c],frame.volts[c])
        self.assertEqual(states[-1]['phase'],'complete')
        self.assertEqual(json.loads((Path(result['output_dir'])/'result.json').read_text(encoding='utf-8'))['completed'],5)
    def test_stop_interrupts_wait_preserves_data_and_releases_device(self):
        state=WebControlState(capture_output_root=self.root)
        state.start_capture({**self.config.to_dict(),'simulate':True,'repeat':dict(enabled=True,count=5,echo_wait_s=10)})
        deadline=time.monotonic()+2
        while (state.snapshot().get('progress') or {}).get('phase')!='waiting' and time.monotonic()<deadline:time.sleep(.005)
        started=time.monotonic();state.stop();status=self.wait(state)
        self.assertLess(time.monotonic()-started,.5)
        self.assertEqual(status['state'],'stopped');self.assertEqual(status['progress']['completed'],1)
        self.assertEqual(len(list(self.root.rglob('*.npz'))),1);self.assertIsNone(state.device)
        state.start_capture({**self.config.to_dict(),'simulate':True})
        self.assertEqual(self.wait(state)['state'],'complete')
    def test_api_worker_reuses_single_device_and_last_save_stem(self):
        state=WebControlState(capture_output_root=self.root);device=Pico4824A(simulate=True)
        with patch.object(state,'_device_for_task',return_value=(device,True)) as factory:
            state.start_capture({**self.config.to_dict(),'simulate':True,'repeat':dict(enabled=True,count=3,echo_wait_s=0)})
            status=self.wait(state)
            self.assertEqual(status['state'],'complete',status['message']);factory.assert_called_once()
        self.assertEqual(len(list(self.root.rglob('*.npz'))),3)
        saved=state.save('npz');self.assertTrue(saved.is_relative_to(self.root))
        self.assertEqual(len(list(self.root.rglob('*.npz'))),3)
        self.assertEqual(state.result.capture_info['repeat_measurement']['index'],3)
    def test_capture_failure_keeps_completed_files_and_error_manifest(self):
        state=WebControlState(capture_output_root=self.root);device=Pico4824A(simulate=True)
        original=device.capture;calls=0
        def capture(config):
            nonlocal calls
            calls+=1
            if calls==2:raise RuntimeError('capture failed')
            return original(config)
        with patch.object(device,'capture',side_effect=capture),patch.object(state,'_device_for_task',return_value=(device,True)):
            state.start_capture({**self.config.to_dict(),'simulate':True,'repeat':dict(enabled=True,count=5,echo_wait_s=0)})
            status=self.wait(state)
        self.assertEqual(status['state'],'error');self.assertIn('capture failed',status['message'])
        self.assertEqual(len(list(self.root.rglob('*.npz'))),1)
        manifest=json.loads(next(self.root.rglob('result.json')).read_text(encoding='utf-8'))
        self.assertEqual(manifest['status'],'error');self.assertEqual(manifest['completed'],1)
    def test_device_initialization_failure_reaches_error(self):
        state=WebControlState(capture_output_root=self.root)
        with patch.object(state,'_device_for_task',side_effect=RuntimeError('open failed')):
            state.start_capture({**self.config.to_dict(),'simulate':True})
            self.assertEqual(self.wait(state)['state'],'error')
