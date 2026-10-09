import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import numpy as np
from pico4824a.bias_scan import *
from pico4824a.bias_scan.safety import TemperatureProvider, TemperatureReading
from pico4824a.config import AcquisitionConfig
from pico4824a.device import Pico4824A
from pico4824a.storage import load_npz
from pico4824a.web import WebControlState

ROOT=Path(__file__).resolve().parents[1]

class FaultPower(SimulatedPowerSupply):
    def __init__(self,fault):super().__init__();self.fault=fault
    def read_actual(self):
        if self.output_state=='on':
            if self.fault=='communication':raise TimeoutError('USB timeout')
            row=super().read_actual()
            if self.fault=='overcurrent':row['current_a']=6.1
            if self.fault=='unstable':row['current_a']=0
            return row
        return super().read_actual()
    def output_off(self):
        if self.fault=='off' and self.output_state=='on':self.output_state='unknown';raise TimeoutError('OFF failed')
        if self.fault=='off' and self.output_state=='unknown':raise TimeoutError('OFF failed')
        super().output_off()

class FakePico:
    def __init__(self,mode='normal'):self.mode=mode;self.stopped=threading.Event();self.started=threading.Event();self.scope=Pico4824A(simulate=True)
    def prepare(self):self.scope.open()
    def stop(self):self.stopped.set()
    def capture(self,config,remaining):
        self.started.set()
        if self.mode=='pico':raise RuntimeError('Pico failure')
        if self.mode=='keyboard':raise KeyboardInterrupt()
        if self.mode=='blocked':self.stopped.wait(2);raise RuntimeError('blocked capture interrupted')
        return self.scope.capture(config)

class BiasScanTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.test-tmp').mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp')
        self.acq=AcquisitionConfig(sample_rate_hz=500000,pre_trigger_samples=50,post_trigger_samples=450)
    def tearDown(self):self.tmp.cleanup()
    def job(self,**kw):
        config=kw.pop('config',BiasScanConfig(start_a=.5,stop_a=.5,repeats=5,interval_s=0,stable_hold_s=.01,poll_s=.01,cooldown_s=0))
        return BiasScanController(config,self.acq,kw.pop('power',SimulatedPowerSupply()),kw.pop('adapter',FakePico()),self.tmp.name,simulate=True,**kw)
    def test_full_default_13_points_130_captures(self):
        job=self.job(config=BiasScanConfig());r=job.run()
        self.assertEqual(r['status'],'complete',r['reason']);self.assertEqual(len(r['summary']),13);self.assertEqual(len(r['runs']),130)
        self.assertEqual(r['best_current_a'],0);self.assertEqual(r['ties_a'],[x*.5 for x in range(13)])
        self.assertEqual(len(list(job.folder.glob('*.npz'))),130)
        self.assertEqual(r['summary'][0]['actual_current_mean_a'],0)
        self.assertFalse(any(e['event']=='on' and e.get('target_a')==0 for e in r['events']))
        self.assertGreaterEqual(r['summary'][1]['capture_batch_duration_s'],.9)
        for i in range(13):
            starts=[e['monotonic_time'] for e in r['events'] if e['event']=='capture_start' and e['point']==i]
            self.assertTrue(all(b-a>=.099 for a,b in zip(starts,starts[1:])))
        loaded=load_npz(job.folder/r['runs'][0]['file']);vpp,reason=BiasScanAnalyzer.vpp(loaded,job.config)
        self.assertEqual(vpp,r['runs'][0]['vpp_v']);self.assertEqual(reason,'')
        self.assertEqual(loaded.config.total_samples,1000)
        self.assertEqual(self.acq.total_samples,500)
        self.assertEqual(json.loads((job.folder/'result.json').read_text())['status'],'complete')
    def test_five_repeats_and_off_before_every_analysis_save(self):
        job=self.job();r=job.run();self.assertEqual(r['status'],'complete');self.assertEqual(len(r['runs']),5)
        powered=False
        for e in r['events']:
            if e['event']=='on':powered=True
            if e['event']=='off_confirmed':powered=False
            if e['event'] in {'analyze','save_waveform','save_task'}:self.assertFalse(powered)
        last=next(i for i,e in enumerate(r['events']) if e['event']=='capture_start' and e['repeat']==5)
        after=[e['event'] for e in r['events'][last+1:] if e['event']!='telemetry']
        self.assertEqual(after[:2],['off_requested','off_confirmed'])
        self.assertAlmostEqual(r['summary'][0]['vpp_sd_v'],0)
    def test_faults(self):
        for fault in ['communication','overcurrent','unstable','off']:
            with self.subTest(fault=fault):
                job=self.job(power=FaultPower(fault));r=job.run()
                self.assertEqual(r['status'],'error');self.assertEqual(r['output_state'],'unknown' if fault=='off' else 'off')
                self.assertTrue(all(not x['eligible'] for x in r['summary']))
    def test_pico_and_ctrl_c(self):
        for fault,state in [('pico','error'),('keyboard','stopped')]:
            job=self.job(adapter=FakePico(fault));r=job.run();self.assertEqual(r['status'],state);self.assertEqual(r['output_state'],'off')
    def test_deadline_independent_of_blocking_capture(self):
        adapter=FakePico('blocked');config=replace(self.job().config,max_on_s=.15,stable_timeout_s=.1)
        job=self.job(config=config,adapter=adapter);start=time.monotonic();r=job.run()
        self.assertEqual(r['status'],'error');self.assertTrue(adapter.stopped.is_set());self.assertLess(time.monotonic()-start,.8)
        self.assertIn('最大通电',r['reason'])
    def test_stop_immediate_off(self):
        adapter=FakePico('blocked');job=self.job(adapter=adapter);thread=threading.Thread(target=job.run);thread.start()
        self.assertTrue(adapter.started.wait(1));job.stop();self.assertEqual(job.power.output_state,'off');thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(job.result['status'],'stopped')
    def test_save_failure_after_off(self):
        job=self.job()
        def fail(*args):self.assertEqual(job.power.output_state,'off');raise OSError('disk full')
        with patch('pico4824a.bias_scan.controller.save_npz',side_effect=fail):r=job.run()
        self.assertEqual(r['status'],'error');self.assertEqual(r['output_state'],'off');self.assertIn('disk full',r['reason'])
    def test_temperature_stale_and_hot(self):
        class Sensor(TemperatureProvider):
            def __init__(self,stale):self.stale=stale
            def read(self):return TemperatureReading(60,time.monotonic()-10 if self.stale else time.monotonic())
        config=replace(self.job().config,cooling_mode='temperature',temperature_limit_c=50,temperature_resume_c=30)
        for stale in [True,False]:
            job=self.job(config=config,temperature=Sensor(stale));r=job.run();self.assertEqual(r['status'],'error');self.assertEqual(r['output_state'],'off');self.assertEqual(len(r['runs']),0)
    def test_partial_point_is_saved_but_not_eligible(self):
        class Partial(FakePico):
            count=0
            def capture(self,config,remaining):
                self.count+=1
                if self.count==3:raise RuntimeError('capture 3 failed')
                return super().capture(config,remaining)
        job=self.job(adapter=Partial());r=job.run()
        self.assertEqual(r['status'],'error');self.assertEqual(len(r['runs']),2)
        self.assertEqual(len(list(job.folder.glob('*.npz'))),2)
        self.assertFalse(r['summary'][0]['eligible']);self.assertIsNone(r['best_current_a'])

    def test_late_monitor_never_enters_next_point(self):
        class SlowPower(SimulatedPowerSupply):
            def read_actual(self):
                if self.output_state=='on':time.sleep(1.2)
                return super().read_actual()
        config=replace(self.job().config,max_on_s=.15,stable_timeout_s=.1)
        job=self.job(config=config,power=SlowPower());r=job.run()
        self.assertEqual(r['status'],'error');self.assertEqual(r['output_state'],'off')
        self.assertTrue(r.get('instrument_cleanup_pending'));self.assertEqual(len(r['summary']),1)
        for thread in job._threads:thread.join(2)

    def test_nonfinite_anywhere_and_incomplete_window_are_invalid(self):
        config=self.job().config;scope=Pico4824A(simulate=True)
        acquisition=config.acquisition(self.acq);sample=scope.capture(acquisition)
        sample.volts['A'][0]=float('nan')
        self.assertIsNone(BiasScanAnalyzer.vpp(sample,config)[0])
        sample=scope.capture(acquisition)
        self.assertIsNone(BiasScanAnalyzer.vpp(sample,replace(config,direct_end_us=3000))[0])
        with self.assertRaises(ValueError):self.job(config=replace(config,memory_limit_mb=.001))

    def test_single_reading_cannot_claim_continuous_stability(self):
        class DelayedSecondReading(SimulatedPowerSupply):
            count=0
            def read_actual(self):
                self.count+=1
                if self.count==2:time.sleep(.2)
                return super().read_actual()
        config=replace(self.job().config,stable_hold_s=.05,stable_timeout_s=.1,max_on_s=.5)
        job=self.job(config=config,power=DelayedSecondReading());result=job.run()
        self.assertEqual(result['status'],'error')
        self.assertEqual(result['output_state'],'off')
        self.assertEqual(len(result['runs']),0)

    def test_validation_and_live_lock(self):
        for raw in [{'stop_a':6.1},{'repeats':4},{'step_a':0},{'start_a':float('nan')},{'max_on_s':True},{'independent_cutoff_confirmed':'yes'}]:
            with self.assertRaises(ValueError):BiasScanConfig.from_dict(raw)
        with self.assertRaises(ValueError):BiasScanConfig().validate(live=True)
        with self.assertRaises(ValueError):self.job(config=replace(self.job().config,direct_end_us=3000))
        ready=replace(self.job().config,voltage_limit_v=40,max_on_s=3,stable_timeout_s=1,cooldown_s=0,inductive_protection_confirmed=True,independent_cutoff_confirmed=True,protection_notes='test')
        control=WebControlState()
        with self.assertRaisesRegex(RuntimeError,'独立'):control.bias_preflight({'simulate':False,'config':self.acq.to_dict(),'bias':__import__('dataclasses').asdict(ready)})
    def test_statistics_invalid_and_ties(self):
        rows=[{'valid':True,'actual_current_a':.5,'vpp_v':v} for v in [1,2,3,4,5]]
        r=BiasScanAnalyzer.summary(.5,rows,5);self.assertEqual(r['vpp_mean_v'],3);self.assertAlmostEqual(r['vpp_sd_v'],np.std([1,2,3,4,5],ddof=1))
        rows[0]['valid']=False;self.assertFalse(BiasScanAnalyzer.summary(.5,rows,5)['eligible'])
        sample=FakePico().scope.capture(self.acq)
        sample.overflow_channels=('A',);self.assertIsNone(BiasScanAnalyzer.vpp(sample,self.job().config)[0])

class PowerDriverTests(unittest.TestCase):
    def test_scpi_model_commands_and_idempotent_off(self):
        class Instrument:
            def __init__(self):self.commands=[];self.v=0;self.i=0;self.on=0
            def query(self,c):
                self.commands.append(c)
                return {'*IDN?':'ITECH, IT6524D, SN, FW','OUTP?':str(self.on),'VOLT?':str(self.v),'CURR?':str(self.i),'MEAS:CURR?':str(self.i),'MEAS:VOLT?':str(self.v)}[c]
            def write(self,c):
                self.commands.append(c)
                if c.startswith('VOLT '):self.v=float(c.split()[1])
                if c.startswith('CURR '):self.i=float(c.split()[1])
                if c=='OUTP ON':self.on=1
                if c=='OUTP OFF':self.on=0
            def close(self):pass
        class Manager:
            def open_resource(self,r):return instrument
            def close(self):pass
        instrument=Instrument();power=IT6524DController('USB::TEST',manager_factory=lambda _:Manager())
        power.connect();power.configure(40,.5);power.output_on();self.assertEqual(power.read_actual()['current_a'],.5)
        power.output_off();power.output_off();self.assertEqual(power.output_state,'off')
        with self.assertRaises(RuntimeError):power.read_actual()
        self.assertEqual(instrument.commands[:4],['*IDN?','SYST:REM','OUTP OFF','OUTP?']);power.close()

if __name__=='__main__':unittest.main()
