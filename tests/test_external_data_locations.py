import hashlib,json,tempfile,unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch
from pico4824a.file_browser import list_directory
from pico4824a.data_locations import remember_selected_directory
from pico4824a.native_data_picker import choose_data_directory
from pico4824a.web import WebControlState
ROOT=Path(__file__).resolve().parents[1]

class ExternalDataLocationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp');self.root=Path(self.temp.name)
  self.project=self.root/'application';self.project.mkdir();(self.project/'data').mkdir()
  self.external=self.root/'USB_实验数据';self.external.mkdir()
  (self.external/'raw').mkdir();(self.external/'raw'/'capture.npz').write_bytes(b'original')
 def tearDown(self):self.temp.cleanup()
 def test_only_native_selected_location_becomes_readable(self):
  with self.assertRaises(ValueError):list_directory(str(self.external),self.project)
  before=hashlib.sha256((self.external/'raw'/'capture.npz').read_bytes()).hexdigest()
  with patch('pico4824a.native_data_picker.subprocess.run',return_value=CompletedProcess([],0,json.dumps({'path':str(self.external)}),'')):
   result=choose_data_directory(self.project)
  self.assertEqual(result['path'],str(self.external.resolve()))
  listing=list_directory(str(self.external/'raw'),self.project)
  self.assertEqual(listing['entries'][0]['name'],'capture.npz')
  self.assertIsNone(list_directory(str(self.external),self.project)['parent'])
  self.assertEqual(before,hashlib.sha256((self.external/'raw'/'capture.npz').read_bytes()).hexdigest())
  self.assertFalse((self.root/'configs').exists())
  self.assertEqual(json.loads((self.project/'configs/data-locations.json').read_text(encoding='utf-8'))['directories'],[str(self.external.resolve())])
  other=self.root/'not_selected';other.mkdir()
  with self.assertRaises(ValueError):list_directory(str(other),self.project)
 def test_cancel_does_not_grant_any_directory(self):
  with patch('pico4824a.native_data_picker.subprocess.run',return_value=CompletedProcess([],0,'{"path":""}','')):
   self.assertTrue(choose_data_directory(self.project)['cancelled'])
  self.assertFalse((self.project/'configs/data-locations.json').exists())
 def test_multiple_locations_and_reconnect_are_not_fixed_to_one_folder(self):
  second=self.root/'Another_USB';second.mkdir();remember_selected_directory(self.external,self.project);remember_selected_directory(second,self.project)
  for folder in [self.external,second]:self.assertEqual(list_directory(str(folder),self.project)['path'],str(folder.resolve()))
  self.assertEqual(len(json.loads((self.project/'configs/data-locations.json').read_text(encoding='utf-8'))['directories']),2)
  second.rmdir()
  with self.assertRaises(ValueError):list_directory(str(second),self.project)
  second.mkdir();self.assertEqual(list_directory(str(second),self.project)['path'],str(second.resolve()))
 def test_lcr_uses_selected_external_directory_without_modifying_it(self):
  remember_selected_directory(self.external,self.project)
  state=WebControlState()
  with patch('pico4824a.web.PROJECT_DIR',self.project):
   for mode in ['small_lcr','big_lcr','linearity']:
    self.assertEqual(state._lcr_analysis_directory(mode,str(self.external)),self.external.resolve())
   self.assertEqual(state._linearity_directory(str(self.external)),self.external.resolve())
  self.assertEqual(sorted(p.name for p in self.external.iterdir()),['raw'])
 def test_quoted_path_from_explorer_is_accepted_after_selection(self):
  remember_selected_directory(self.external,self.project)
  self.assertEqual(list_directory('"'+str(self.external)+'"',self.project)['path'],str(self.external.resolve()))
 def test_native_picker_failure_is_clear_and_does_not_grant(self):
  with patch('pico4824a.native_data_picker.subprocess.run',return_value=CompletedProcess([],1,'','no display')):
   with self.assertRaisesRegex(RuntimeError,'无桌面'):choose_data_directory(self.project)
  self.assertFalse((self.project/'configs/data-locations.json').exists())
