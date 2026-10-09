"""Read-only scientific equivalence check against the local reference, using real A1.

Run with python -B. No reference exporter or hardware operation is invoked.
"""
import importlib.util
import json
from pathlib import Path
import sys
import numpy as np
from pico4824a.analysis import process_dataset, load_dataset, fft_bandpass
from pico4824a.time_frequency import time_frequency_map

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT.with_name('Pico_4824A')/'pico4824a'
spec=importlib.util.spec_from_file_location('reference_pico',SOURCE/'__init__.py',submodule_search_locations=[str(SOURCE)])
package=importlib.util.module_from_spec(spec);sys.modules['reference_pico']=package;spec.loader.exec_module(package)
from reference_pico.analysis import process_dataset as original_process, fft_bandpass as original_filter
from reference_pico.time_frequency import time_frequency_map as original_tf

path=ROOT/'data/A1.npz'
checks=[]
for enabled in (False,True):
    payload={'path':str(path),'channels':['A'],'show_raw':True,'show_filtered':True,
             'filter':{'enabled':enabled,'low_hz':60000,'high_hz':90000,'transition_hz':5000},
             'spectrum':{'source':'filtered' if enabled else 'raw','mode':'amplitude','window_function':'hann','windows':[{'label':'全记录','start_us':-100,'end_us':899.975}]}}
    a=process_dataset(path,payload);b=original_process(path,payload)
    assert json.dumps(a,sort_keys=True,allow_nan=False)==json.dumps(b,sort_keys=True,allow_nan=False)
    checks.append('Raw/Filtered/FFT '+str(enabled))
t,channels,meta=load_dataset(path);fs=float(meta['sample_rate_hz'])
filtered=fft_bandpass(channels['A'],fs,60000,90000,5000)
np.testing.assert_array_equal(filtered,original_filter(channels['A'],fs,60000,90000,5000))
for method in ['stft','wpd','cwt']:
    config={'method':method,'start_us':-100,'end_us':899.975,'frequency_min_hz':20000,'frequency_max_hz':180000,'floor_db':-60,'window_us':80,'overlap_ratio':.75,'fft_samples':4096,'window_function':'hann','wpd_level':0,'wavelet':'db4','cwt_bins':48,'morlet_omega0':6,'cwt_log_frequency':False}
    a=time_frequency_map(t,channels['A'],config);b=original_tf(t,channels['A'],config)
    assert json.dumps(a,sort_keys=True,allow_nan=False)==json.dumps(b,sort_keys=True,allow_nan=False)
    checks.append(method.upper())
print(json.dumps({'real_input':str(path),'identical_results':checks},ensure_ascii=False,indent=2))
