"""The local launcher can stop the server without touching real hardware."""

from http import HTTPStatus
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from urllib.request import Request, urlopen

from pico4824a.web import PicoWebHandler, ThreadingHTTPServer


class LauncherEncodingTests(unittest.TestCase):
    def test_windows_powershell_launcher_has_utf8_bom(self) -> None:
        launcher = Path(__file__).resolve().parents[1] / "start_web_lan.ps1"
        self.assertTrue(launcher.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_launcher_avoids_powershell_smart_quote_tokens(self) -> None:
        launcher = Path(__file__).resolve().parents[1] / "start_web_lan.ps1"
        text = launcher.read_text(encoding="utf-8-sig")
        self.assertFalse(set(text) & set("“”‘’"))


class LocalAdminShutdownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), PicoWebHandler)
        self.control = SimpleNamespace(
            _lock=threading.RLock(),
            status=SimpleNamespace(state="idle"),
            shutdown=Mock(),
        )
        self.server.control = self.control
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def direct_request(self, headers: dict[str, str], path: str = "/api/admin/shutdown") -> Mock:
        handler = PicoWebHandler.__new__(PicoWebHandler)
        handler.server = self.server
        handler.path = path
        handler.client_address = ("127.0.0.1", 9999)
        handler.headers = headers
        handler._send_json = Mock()
        handler.do_POST()
        return handler._send_json

    def request_shutdown(self):
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}/api/admin/shutdown",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-Pico-Local-Control": "shutdown",
            },
            method="POST",
        )
        return urlopen(request, timeout=3)

    def test_rejects_request_without_local_control_header(self) -> None:
        send = self.direct_request({"Host": "127.0.0.1"})
        self.assertEqual(send.call_args.args[1], HTTPStatus.FORBIDDEN)
        self.control.shutdown.assert_not_called()

    def test_rejects_shutdown_during_measurement(self) -> None:
        self.control.status.state = "running"
        send = self.direct_request({"Host": "127.0.0.1", "X-Pico-Local-Control": "shutdown"})
        self.assertEqual(send.call_args.args[1], HTTPStatus.CONFLICT)
        self.control.shutdown.assert_not_called()

    def test_rejects_browser_origin_even_with_control_header(self) -> None:
        send = self.direct_request({
            "Host": "127.0.0.1",
            "X-Pico-Local-Control": "shutdown",
            "Origin": "http://untrusted.example",
        })
        self.assertEqual(send.call_args.args[1], HTTPStatus.FORBIDDEN)
        self.control.shutdown.assert_not_called()

    def test_rejects_new_tasks_during_shutdown(self) -> None:
        self.server.shutdown_in_progress = True
        send = self.direct_request({}, path="/api/capture")
        self.assertEqual(send.call_args.args[1], HTTPStatus.SERVICE_UNAVAILABLE)

    def test_shutdown_calls_control_cleanup_and_stops_http_server(self) -> None:
        with self.request_shutdown() as response:
            self.assertEqual(json.load(response)["ok"], True)
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())
        self.control.shutdown.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
