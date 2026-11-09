"""Transport disconnects must not turn into a second HTTP error response."""

from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pico4824a.web import PicoWebHandler


class WebDisconnectTests(unittest.TestCase):
    def make_handler(self, path):
        handler = PicoWebHandler.__new__(PicoWebHandler)
        handler.path = path
        handler.server = SimpleNamespace()
        handler._send_json = Mock()
        return handler

    def test_asset_disconnect_does_not_send_bad_request(self):
        handler = self.make_handler("/measure")
        handler._send_asset = Mock(side_effect=ConnectionAbortedError(10053, "disconnected"))
        handler.do_GET()
        handler._send_json.assert_not_called()
        self.assertTrue(handler.close_connection)

    def test_post_disconnect_does_not_send_bad_request(self):
        handler = self.make_handler("/api/awg-preview")
        handler._read_json = Mock(side_effect=ConnectionResetError("disconnected"))
        handler.do_POST()
        handler._send_json.assert_not_called()
        self.assertTrue(handler.close_connection)

    def test_disconnect_while_sending_error_is_contained(self):
        handler = self.make_handler("/measure")
        handler._send_asset = Mock(side_effect=ValueError("bad input"))
        handler._send_json.side_effect = BrokenPipeError("disconnected")
        with patch.object(BaseHTTPRequestHandler, "handle", lambda _: handler.do_GET()):
            handler.handle()
        handler._send_json.assert_called_once()
        self.assertTrue(handler.close_connection)

    def test_application_error_still_returns_400(self):
        handler = self.make_handler("/measure")
        handler._send_asset = Mock(side_effect=ValueError("bad input"))
        handler.do_GET()
        handler._send_json.assert_called_once_with({"error": "bad input"}, 400)


if __name__ == "__main__":
    unittest.main()
