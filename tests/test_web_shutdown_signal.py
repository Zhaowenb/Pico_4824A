"""Check real POSIX service termination without opening either instrument."""
import os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
@unittest.skipUnless(os.name=='posix','SIGTERM integration requires Linux/POSIX')
class WebSignalTests(unittest.TestCase):
    def test_sigterm_runs_hardware_cleanup(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as tmp:
            marker=Path(tmp)/'shutdown.txt'
            code='''from pathlib import Path
from pico4824a import web
class FakeControl:
    def initialize_hardware(self):pass
    def shutdown(self):Path(MARKER).write_text('OFF cleanup called')
web.WebControlState=FakeControl
web.serve('127.0.0.1',0)
'''.replace('MARKER',repr(str(marker)))
            proc=subprocess.Popen([sys.executable,'-B','-u','-c',code],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                self.assertIn('Web',proc.stdout.readline());time.sleep(.1);proc.terminate();out,err=proc.communicate(timeout=10)
                self.assertEqual(proc.returncode,0,err);self.assertEqual(marker.read_text(),'OFF cleanup called')
            finally:
                if proc.poll() is None:proc.kill();proc.wait()
if __name__=='__main__':unittest.main()
