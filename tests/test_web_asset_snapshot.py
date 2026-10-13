import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen
from pico4824a.web import PicoWebHandler, ThreadingHTTPServer, snapshot_web_assets
ROOT=Path(__file__).resolve().parents[1]

class WebAssetSnapshotTests(unittest.TestCase):
    def test_running_server_does_not_mix_revisions(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as folder:
            root=Path(folder);(root/'ui').mkdir();(root/'interference').mkdir()
            files={'index.html':b'old html','app.js':b'old script','ui/analysis-state.js':b'old dependency','interference/index.html':b'old experiment'}
            for name,body in files.items():(root/name).write_bytes(body)
            with patch('pico4824a.web.WEB_DIR',root):assets=snapshot_web_assets()
            server=ThreadingHTTPServer(('127.0.0.1',0),PicoWebHandler);server.web_assets=assets
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                for name in files:(root/name).write_bytes(b'new revision')
                for url,name in [('/lcr-linearity','index.html'),('/app.js','app.js'),('/ui/analysis-state.js','ui/analysis-state.js'),('/interference','interference/index.html')]:
                    with urlopen(f'http://127.0.0.1:{server.server_port}'+url) as response:
                        self.assertEqual(response.read(),files[name])
                with patch('pico4824a.web.WEB_DIR',root):
                    fresh=snapshot_web_assets()
                self.assertEqual(fresh['index.html'],b'new revision')
            finally:server.shutdown();server.server_close();thread.join(2)
