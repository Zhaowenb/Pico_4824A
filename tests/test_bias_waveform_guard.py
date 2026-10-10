import unittest,tempfile,threading
from pathlib import Path
from dataclasses import replace
import numpy as np
from pico4824a.bias_scan import BiasScanConfig,BiasScanController,SimulatedPowerSupply
from pico4824a.config import AcquisitionConfig
from pico4824a.device import CaptureResult
from pico4824a.waveforms import normalized_waveform
from pico4824a.bias_scan.quality import assess

ROOT=Path(__file__).resolve().parents[1]
def setup():
    acq=AcquisitionConfig(sample_rate_hz=2000000,pre_trigger_samples=200,post_trigger_samples=3800)
    acq.awg.frequency_hz=50000;acq.awg.pk_to_pk_v=1
    for c in 'ABC':acq.channels[c].enabled=True;acq.channels[c].range='2V'
    cfg=BiasScanConfig(start_a=.5,stop_a=.5,repeats=5,interval_s=0,stable_hold_s=.01,poll_s=.01,cooldown_s=0,
        waveform_guard_enabled=True,excitation_voltage_channel='B',excitation_current_channel='C',
        excitation_start_us=-5,excitation_end_us=115,direct_start_us=300,direct_end_us=800,filter_enabled=False,
        auto_range_enabled=False)
    return acq,cfg

def signal(acq,mode='',h2=.1):
    fs=acq.sample_rate_hz;t=(np.arange(acq.total_samples)-acq.pre_trigger_samples)/fs
    template,rep=normalized_waveform(acq.awg);source=np.arange(len(template))/len(template)/rep
    wave=np.interp(t,source,template,left=0,right=0)*acq.awg.pk_to_pk_v/2
    volts={'B':wave.copy(),'C':wave.copy(),'A':.1*(np.sin(2*np.pi*50000*t)+h2*np.sin(2*np.pi*100000*t))}
    overflow=()
    if mode=='flat':volts['B']=np.clip(wave,-.2,.2)
    if mode=='break':volts['B'][t>45e-6]=0
    if mode=='lost':volts['B']*=0
    if mode=='ipp':
        acq.channels['C'].range='50V';volts['C']*=20
    if mode=='adc':overflow=('B',)
    return CaptureResult(t,volts,1/fs,fs,fs,overflow,acq,True)

class Adapter:
    def __init__(self,power,mode=''):self.power=power;self.mode=mode;self.count=0
    def prepare(self):pass
    def stop(self):pass
    def capture(self,acq,remaining):
        self.count+=1
        if self.mode=='pico':raise RuntimeError('capture failure')
        mode='lost' if self.mode=='lost_later' and self.count==2 else self.mode
        if self.mode=='retry':mode='adc' if self.count==1 else ''
        return signal(acq,mode)

class GuardTests(unittest.TestCase):
    def test_diagnosis(self):
        a,c=setup()
        for mode,expected in [('', 'OK'),('flat','AMPLIFIER_CLIP'),('break','SHAPE_FAULT'),('lost','SIGNAL_LOST'),('ipp','CURRENT_LIMIT'),('adc','ADC_RANGE')]:
            with self.subTest(mode=mode):self.assertEqual(assess(signal(a,mode),a,c)['status'],expected)
    def test_harmonic_ratio_and_threshold(self):
        a,c=setup();c=replace(c,h2_enabled=True,h2_limit_pct=20)
        q=assess(signal(a),a,c);self.assertEqual(q['status'],'OK',q);self.assertAlmostEqual(q['receiver_h2_h1_pct'],10,delta=.2)
        self.assertEqual(assess(signal(a),a,replace(c,h2_limit_pct=5))['status'],'H2_LIMIT')
    def test_off_precedes_all_diagnosis_and_saves(self):
        a,c=setup();c=replace(c,amplitude_mode='sweep',amplitude_start_vpp=.5,amplitude_stop_vpp=1,amplitude_step_vpp=.5)
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as tmp:
            power=SimulatedPowerSupply();job=BiasScanController(c,a,power,Adapter(power),tmp,simulate=True);r=job.run()
            self.assertEqual(r['status'],'complete',r['reason']);self.assertEqual(len(r['summary']),2);self.assertEqual(len(r['runs']),10)
            powered=False
            for e in r['events']:
                if e['event']=='on':powered=True
                if e['event']=='off_confirmed':powered=False
                if e['event'] in {'quality_check','save_waveform','save_task'}:self.assertFalse(powered)
            self.assertEqual(power.output_state,'off')
            self.assertEqual(len(list(job.folder.rglob('*.npz'))),10)
    def test_faults_shutdown_and_preserve(self):
        for mode in ['flat','break','lost_later','ipp','pico']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as tmp:
                a,c=setup();power=SimulatedPowerSupply();adapter=Adapter(power,mode)
                job=BiasScanController(c,a,power,adapter,tmp,simulate=True);r=job.run()
                self.assertEqual(r['status'],'error',r['reason']);self.assertEqual(power.output_state,'off');self.assertIn('safety_alarm',r)
                if mode!='pico':self.assertTrue(list(job.folder.rglob('*.npz')))
    def test_adc_retry(self):
        a,c=setup();c=replace(c,auto_range_enabled=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as tmp:
            power=SimulatedPowerSupply();job=BiasScanController(c,a,power,Adapter(power,'retry'),tmp,simulate=True);r=job.run()
            self.assertEqual(r['status'],'complete',r['reason']);self.assertEqual(len(r['runs']),6);self.assertTrue(r['runs'][0]['range_retry']);self.assertEqual(r['summary'][0]['valid_count'],5)
    def test_off_failure_and_save_failure(self):
        from unittest.mock import patch
        class FailedOff(SimulatedPowerSupply):
            def output_off(self):
                if self.output_state in {'on','unknown'}:
                    self.output_state='unknown';raise TimeoutError('OFF failed')
                super().output_off()
        for mode in ['off','save']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as tmp:
                a,c=setup();power=FailedOff() if mode=='off' else SimulatedPowerSupply()
                job=BiasScanController(c,a,power,Adapter(power),tmp,simulate=True)
                if mode=='save':
                    with patch('pico4824a.bias_scan.guarded.save_npz',side_effect=OSError('disk failed')):r=job.run()
                else:r=job.run()
                self.assertEqual(r['status'],'error');self.assertEqual(r['output_state'],'unknown' if mode=='off' else 'off')
                self.assertEqual(r['safety_alarm']['output_state'],r['output_state'])
                if mode=='off':self.assertTrue(list(job.folder.rglob('*.npz')))
    def test_blocked_capture_deadline(self):
        class Blocked(Adapter):
            def __init__(self,power):super().__init__(power);self.event=threading.Event()
            def capture(self,acq,remaining):self.event.wait(2);raise RuntimeError('capture unblocked')
            def stop(self):self.event.set()
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as tmp:
            a,c=setup();c=replace(c,max_on_s=.15,stable_timeout_s=.05)
            power=SimulatedPowerSupply();job=BiasScanController(c,a,power,Blocked(power),tmp,simulate=True);r=job.run()
            self.assertEqual(r['status'],'error');self.assertEqual(power.output_state,'off')
            self.assertTrue(any(e['event']=='safety_trip' for e in r['events']))
    def test_preflight(self):
        a,c=setup()
        with self.assertRaises(ValueError):replace(c,excitation_ipp_limit_a=13).validate()
        with self.assertRaises(ValueError):replace(c,excitation_end_us=30).acquisition(a)
        with self.assertRaises(ValueError):replace(c,amplitude_mode='fixed',waveform_guard_enabled=False).validate()

if __name__=='__main__':unittest.main()
