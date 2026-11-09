"""Real local Git fixtures; no network and no physical instruments."""
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import threading
import unittest
from pico4824a.software_update import SoftwareUpdater

ROOT = Path(__file__).resolve().parents[1]

@dataclass
class Status:
    state:str='idle'
    task_kind:str='capture'
    message:str='ready'

class Control:
    def __init__(self):
        self._lock=threading.RLock();self.status=Status();self._worker_thread=None
        self.bias_unknown=False;self.bias_cleanup_pending=False;self.bias_power=None
        self.bias_controller=None;self.events=[];self.fail_shutdown=False
    def shutdown(self):
        self.events.append('OFF and close')
        if self.fail_shutdown:
            self.bias_unknown=True
            raise RuntimeError('OFF failed')

class SoftwareUpdateTests(unittest.TestCase):
    def git(self, root, *args):
        p=subprocess.run(['git','-c',f'safe.directory={root}','-C',str(root),*args],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        return p.stdout.strip()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp');self.addCleanup(self.tmp.cleanup)
        self.remote=Path(self.tmp.name)/'remote';self.remote.mkdir()
        self.git(self.remote,'init','-b','btf')
        self.git(self.remote,'config','user.email','tests@localhost');self.git(self.remote,'config','user.name','Tests')
        (self.remote/'.gitignore').write_text('data/\nconfigs/software-updates.json\n')
        (self.remote/'version.txt').write_text('before')
        self.commit()
        self.local=Path(self.tmp.name)/'target'
        self.git(Path(self.tmp.name),'clone',str(self.remote),str(self.local))
        self.control=Control();self.restarted=[]
        self.updater=SoftwareUpdater(self.local,self.control,lambda:self.restarted.append(True),str(self.remote))
        self.updater.snapshot()
    def commit(self):
        self.git(self.remote,'add','.');self.git(self.remote,'commit','-m','fixture update')
    def check(self):
        self.updater.check();self.updater._thread.join(10)
        self.assertFalse(self.updater._thread.is_alive())
        return self.updater.snapshot()
    def new_version(self):
        (self.remote/'version.txt').write_text('after');self.commit()
    def test_available_and_fast_forward_after_safe_shutdown(self):
        self.new_version();s=self.check();self.assertTrue(s['available'],s)
        (self.local/'data').mkdir();(self.local/'data/measurement.npz').write_bytes(b'unchanged')
        self.updater.apply();self.updater._thread.join(10)
        self.assertEqual(self.updater.snapshot()['phase'],'restarting')
        self.assertEqual(self.control.events,['OFF and close']);self.assertTrue(self.restarted)
        self.assertEqual((self.local/'version.txt').read_text(),'after')
        self.assertEqual((self.local/'data/measurement.npz').read_bytes(),b'unchanged')
    def test_current_and_auto_preference_persists_without_dirtying_git(self):
        self.assertEqual(self.check()['phase'],'current')
        self.updater.preferences({'auto_check':False})
        other=SoftwareUpdater(self.local,self.control, trusted_remote=str(self.remote))
        self.assertFalse(other.snapshot()['auto_check'])
        self.assertEqual(self.git(self.local,'status','--porcelain'),'')
    def test_local_modification_is_preserved_and_update_blocked(self):
        self.new_version();(self.local/'version.txt').write_text('my changes')
        s=self.check();self.assertTrue(s['available']);self.assertTrue(s['blocked'])
        with self.assertRaises(RuntimeError):self.updater.apply()
        self.assertEqual((self.local/'version.txt').read_text(),'my changes')
        self.assertEqual(self.control.events,[])

    def test_check_interval_bounds_and_persistence(self):
        for value in [60,3600,31536000]:
            self.updater.preferences({'auto_check':True,'check_interval_s':value})
            restored=SoftwareUpdater(self.local,self.control,trusted_remote=str(self.remote))
            self.assertEqual(restored.snapshot()['check_interval_s'],value)
        for value in [0,59,31536001,True,1.5,'3600',None]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.updater.preferences({'auto_check':True,'check_interval_s':value})
        self.updater.preferences({'auto_check':False})
        self.assertEqual(self.updater.snapshot()['check_interval_s'],31536000)

    def test_legacy_preference_uses_one_hour_and_preserves_disabled(self):
        self.updater.preference_path.parent.mkdir(parents=True,exist_ok=True)
        self.updater.preference_path.write_text('{"auto_check":false}')
        restored=SoftwareUpdater(self.local,self.control,trusted_remote=str(self.remote))
        self.assertFalse(restored.snapshot()['auto_check'])
        self.assertEqual(restored.snapshot()['check_interval_s'],3600)
    def test_busy_and_unknown_output_do_not_update(self):
        self.new_version();self.check()
        for state in ['running','paused']:
            self.control.status.state=state
            with self.assertRaises(RuntimeError):self.updater.apply()
        self.control.status.state='idle';self.control.bias_unknown=True
        with self.assertRaises(RuntimeError):self.updater.apply()
        self.assertEqual(self.control.events,[])
    def test_shutdown_failure_prevents_merge_and_restart(self):
        self.new_version();self.check();self.control.fail_shutdown=True
        self.updater.apply();self.updater._thread.join(10)
        self.assertEqual(self.updater.snapshot()['phase'],'error')
        self.assertEqual((self.local/'version.txt').read_text(),'before')
        self.assertFalse(self.restarted);self.assertTrue(self.control.bias_unknown)
    def test_dependencies_require_explicit_environment_update(self):
        (self.remote/'requirements.txt').write_text('new dependency');self.commit()
        s=self.check();self.assertTrue(s['available']);self.assertIn('运行环境',s['blocked'])
        with self.assertRaises(RuntimeError):self.updater.apply()
    def test_source_and_wrong_branch_are_rejected(self):
        with self.assertRaises(RuntimeError):SoftwareUpdater(ROOT.parent/'Pico_4824A',self.control)
        self.git(self.local,'switch','-c','other')
        s=self.check();self.assertEqual(s['phase'],'error');self.assertIn('分支',s['blocked'])
    def test_revision_change_since_check_is_rejected(self):
        self.new_version();self.check()
        (self.local/'untracked.txt').write_text('local file')
        self.updater.apply();self.updater._thread.join(10)
        self.assertEqual(self.updater.snapshot()['phase'],'error')
        self.assertEqual(self.control.events,[])
