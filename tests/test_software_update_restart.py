"""Verify restart wiring without starting a socket, process or instrument."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from pico4824a.web import serve

class SoftwareUpdateRestartTests(unittest.TestCase):
    def test_restart_executes_same_host_port_after_socket_close(self):
        control=Mock()
        server=SimpleNamespace(serve_forever=Mock(),server_close=Mock(),shutdown=Mock())
        def finish(**_):
            server.restart_after_update=True
            server.update_hardware_closed=True
        server.serve_forever.side_effect=finish
        with patch('pico4824a.web.ThreadingHTTPServer',return_value=server), \
             patch('pico4824a.web.WebControlState',return_value=control), \
             patch('pico4824a.web.SoftwareUpdater'), \
             patch('pico4824a.web.snapshot_web_assets',return_value={}), \
             patch('pico4824a.web.os.execv') as execute:
            execute.side_effect=lambda *_:self.assertTrue(server.server_close.called)
            serve('127.0.0.1',4899)
        control.shutdown.assert_not_called() # Updater already confirmed OFF and closed hardware.
        args=execute.call_args.args[1]
        self.assertEqual(args[-5:],['web','--host','127.0.0.1','--port','4899'])

    def test_normal_shutdown_closes_hardware_without_restart(self):
        control=Mock()
        server=SimpleNamespace(serve_forever=Mock(),server_close=Mock(),shutdown=Mock())
        with patch('pico4824a.web.ThreadingHTTPServer',return_value=server), \
             patch('pico4824a.web.WebControlState',return_value=control), \
             patch('pico4824a.web.SoftwareUpdater'), \
             patch('pico4824a.web.snapshot_web_assets',return_value={}), \
             patch('pico4824a.web.os.execv') as execute:
            serve('127.0.0.1',4899)
        control.shutdown.assert_called_once();execute.assert_not_called()
