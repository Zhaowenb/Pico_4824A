import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from pico4824a import storage_naming as naming
from pico4824a.web import WebControlState
from pico4824a.config import AcquisitionConfig
from pico4824a.device import Pico4824A
from pico4824a.storage import load_npz

ROOT=Path(__file__).resolve().parents[1]


class StorageNamingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp')
        self.folder=Path(self.temp.name)
        self.settings=patch.object(naming,'SETTINGS',self.folder/'names.json')
        self.settings.start()

    def tearDown(self):
        self.settings.stop();self.temp.cleanup()

    def test_preferences_safe_and_persistent(self):
        values=naming.save_preferences({'capture':'试件01_直达波','bias':'温度20C_偏置扫描'})
        self.assertEqual(values,naming.preferences())
        self.assertEqual(json.loads(naming.SETTINGS.read_text(encoding='utf-8'))['capture'],'试件01_直达波')
        for value in ['../source','CON','x/y','NUL.txt','a:b','',None,'a'*65]:
            with self.assertRaises(ValueError):naming.save_preferences({'capture':value})
        self.assertEqual(naming.preferences()['capture'],'试件01_直达波')

    def test_unique_readable_directory_and_readonly_source(self):
        paths=[naming.session_directory(self.folder,'bias','0-6A_步进0.5A_10次',name='试件01',simulated=True) for _ in range(3)]
        self.assertEqual(len(set(paths)),3)
        self.assertTrue(all('试件01__' in p.name and '仿真' in p.name for p in paths))
        with self.assertRaises(ValueError):naming.session_directory(ROOT.with_name('Pico_4824A')/'data','bias')
        with self.assertRaises(ValueError):naming.output_path(ROOT.with_name('Pico_4824A')/'data/test.npz')

    def test_capture_formats_share_session_and_preserve_data(self):
        scope=Pico4824A(simulate=True);scope.open()
        result=scope.capture(AcquisitionConfig(sample_rate_hz=500000,pre_trigger_samples=20,post_trigger_samples=80));scope.close()
        control=WebControlState();control.result=result
        with patch('pico4824a.web.capture_stem',lambda value:naming.capture_stem(value,self.folder)):
            npz=control.save('npz');csv=control.save('csv')
            self.assertEqual(npz.parent,csv.parent)
            self.assertEqual(npz.stem,csv.stem)
            self.assertIn('0.5MSps',npz.stem)
            loaded=load_npz(npz)
            self.assertEqual(loaded.config.to_dict(),result.config.to_dict())
            self.assertTrue((loaded.time_s==result.time_s).all())
            for name in result.volts:self.assertTrue((loaded.volts[name]==result.volts[name]).all())
        control.shutdown()

    def test_bias_archive_hierarchy_and_bytes(self):
        from pico4824a.bias_scan import BiasScanConfig, BiasScanController, SimulatedPowerSupply
        from test_bias_scan import FakePico
        cfg=BiasScanConfig(stop_a=.5,repeats=5,interval_s=0,stable_hold_s=.01,poll_s=.01,cooldown_s=0,scan_name='试件02')
        acq=AcquisitionConfig(sample_rate_hz=500000,pre_trigger_samples=50,post_trigger_samples=450)
        job=BiasScanController(cfg,acq,SimulatedPowerSupply(),FakePico(),self.folder,simulate=True)
        result=job.run();self.assertEqual(result['status'],'complete',result['reason'])
        self.assertEqual(len(list(job.folder.glob('*_电流*'))),2)
        self.assertEqual(len(list(job.folder.rglob('*.npz'))),10)
        for row in result['runs']:
            self.assertIn(f"电流{row['target_a']:.3f}A",row['file'])
            self.assertTrue((job.folder/row['file']).is_file())
            self.assertTrue((job.folder/row['file']).parent.joinpath('summary.json').is_file())
        control=WebControlState();control.bias_controller=job
        archive=control.bias_archive()
        with ZipFile(archive) as stream:
            for path in job.folder.rglob('*'):
                if path.is_file():self.assertEqual(stream.read((Path(job.folder.name)/path.relative_to(job.folder)).as_posix()),path.read_bytes())
        control.status.state='running'
        with self.assertRaises(RuntimeError):control.bias_archive()
        control.status.state='idle';control.shutdown()
