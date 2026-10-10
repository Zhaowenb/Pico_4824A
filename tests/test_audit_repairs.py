import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from zipfile import ZipFile
from pico4824a.config import AcquisitionConfig
from pico4824a import data_access, data_sessions, interference
from pico4824a.analysis import export_filtered
from pico4824a.web import WebControlState

ROOT=Path(__file__).resolve().parents[1]


class AuditRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp')
        self.root=Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()

    def test_config_cannot_write_reference(self):
        with self.assertRaises(ValueError):
            AcquisitionConfig().save(ROOT.with_name('Pico_4824A')/'forbidden.json')

    def test_import_is_idempotent_and_does_not_modify_input(self):
        source=self.root/'reference';source.mkdir();(source/'raw.npz').write_bytes(b'original')
        before=hashlib.sha256((source/'raw.npz').read_bytes()).hexdigest()
        target=data_access.import_directory(source,self.root/'imports')
        self.assertEqual(target,data_access.import_directory(source,self.root/'imports'))
        (target/'raw.npz').write_bytes(b'derived')
        self.assertEqual(before,hashlib.sha256((source/'raw.npz').read_bytes()).hexdigest())
        self.assertTrue(json.loads((target/'import_origin.json').read_text())['source_readonly'])

    def test_lcr_reference_selection_imports_to_workspace(self):
        source=self.root/'reference/lcr_big/job';source.mkdir(parents=True)
        (source/'big_lcr_config.json').write_text('{}')
        control=WebControlState(big_lcr_output_root=self.root/'working/lcr_big')
        with patch.object(data_access,'REFERENCE_DATA',self.root/'reference'):
            target=control._lcr_analysis_directory('big_lcr',str(source))
            legacy=control._linearity_directory(str(source))
        self.assertEqual(target,legacy)
        self.assertTrue(target.is_relative_to(self.root/'working'))
        control.shutdown()

    def test_legacy_interference_session_import(self):
        sid='a'*32;source=self.root/'legacy'/sid;source.mkdir(parents=True)
        (source/'session.json').write_text(json.dumps({'id':sid,'runs':[]}))
        target=self.root/'working'
        with patch.object(interference,'ROOT',target),patch.object(data_access,'LEGACY_INTERFERENCE',self.root/'legacy'):
            self.assertEqual(interference.load_session(sid,target)['id'],sid)
            folder=interference._folder(sid,target)
            self.assertTrue(folder.is_relative_to(target))
        self.assertEqual(list(source.iterdir()),[source/'session.json'])

    def test_analysis_formats_share_directory_and_preserve_values(self):
        source=self.root/'signal.npz';t=np.arange(2000)/2e6;v=np.sin(2*np.pi*70000*t)
        np.savez_compressed(source,time_s=t,ch_A_v=v,metadata_json=json.dumps({'sample_rate_hz':2e6}))
        with patch.object(data_sessions,'PROJECT',self.root):
            npz=export_filtered(source,{'low_hz':60000,'high_hz':90000},'npz')
            csv=export_filtered(source,{'low_hz':60000,'high_hz':90000},'csv')
            self.assertEqual(npz.parent,csv.parent)
            with np.load(npz) as saved:
                matrix=np.loadtxt(csv,delimiter=',',skiprows=1)
                np.testing.assert_allclose(matrix[:,1],saved['ch_A_v'],rtol=1e-9,atol=1e-10)
                np.testing.assert_array_equal(saved['time_s'],t)
            different=export_filtered(source,{'low_hz':50000,'high_hz':80000},'npz')
            self.assertNotEqual(npz.parent,different.parent)

    def test_common_archive_preserves_all_bytes(self):
        folder=self.root/'可读任务';(folder/'电流0.500A').mkdir(parents=True)
        (folder/'config.json').write_bytes(b'{"original":true}')
        (folder/'电流0.500A/raw.npz').write_bytes(b'original signal bytes')
        archive=data_sessions.archive_session(folder)
        with ZipFile(archive) as stream:
            for file in folder.rglob('*'):
                if file.is_file():
                    self.assertEqual(stream.read((Path(folder.name)/file.relative_to(folder)).as_posix()),file.read_bytes())
        self.assertFalse(archive.with_suffix('.zip.tmp').exists())

if __name__=='__main__': unittest.main()
