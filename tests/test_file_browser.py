from pathlib import Path
import tempfile,unittest
from pico4824a.file_browser import list_directory
class FileBrowserTests(unittest.TestCase):
 def test_listing_selection_and_parent(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'data').mkdir();(root/'data'/'capture.npz').write_bytes(b'data');(root/'data'/'note.txt').write_text('ignored');(root/'data'/'folder').mkdir()
   result=list_directory('',root)
   self.assertEqual([e['name'] for e in result['entries']],['folder','capture.npz'])
   self.assertEqual(result['parent'],str(root.resolve()))
   self.assertEqual(list_directory(str(root/'data'/'capture.npz'),root)['path'],str((root/'data').resolve()))
   self.assertEqual(list_directory('data/folder',root)['entries'],[])
   self.assertIsNone(list_directory(str(root),root)['parent'])
 def test_outside_root_is_rejected(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory)
   with self.assertRaises(ValueError):list_directory(str(root.parent),root)
 def test_listing_does_not_write(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'data').mkdir();p=root/'data'/'capture.csv';p.write_text('signal')
   before=p.stat().st_mtime_ns;list_directory('',root)
   self.assertEqual(p.read_text(),'signal');self.assertEqual(p.stat().st_mtime_ns,before)
if __name__=='__main__':unittest.main()
