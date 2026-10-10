"""Discovery refresh regression tests: no real USB or supply commands."""
import types
import unittest
from unittest.mock import Mock, patch

from pico4824a.bias_scan.power import IT6524DController, refresh_linux_usb_context


class UsbHotplugTests(unittest.TestCase):
    def backend(self):
        previous = types.SimpleNamespace(lib=object(), close=Mock())
        renewed = types.SimpleNamespace(lib=previous.lib)
        module = types.ModuleType('usb.backend.libusb1')
        module.get_backend = Mock(return_value=previous)
        module._LibUSB = Mock(return_value=renewed)
        module._lib_object = previous
        parent = types.ModuleType('usb.backend')
        parent.libusb1 = module
        usb = types.ModuleType('usb')
        usb.backend = parent
        return {'usb': usb, 'usb.backend': parent, 'usb.backend.libusb1': module}, module, previous, renewed

    def test_linux_refresh_retains_existing_live_context(self):
        modules, backend, previous, renewed = self.backend()
        with patch('sys.platform', 'linux'), patch.dict('sys.modules', modules):
            refresh_linux_usb_context('@py')
        self.assertIs(backend._lib_object, renewed)
        backend._LibUSB.assert_called_once_with(previous.lib)
        previous.close.assert_not_called()

    def test_windows_and_explicit_native_backend_are_unchanged(self):
        modules, backend, previous, _ = self.backend()
        with patch.dict('sys.modules', modules):
            with patch('sys.platform', 'win32'):
                refresh_linux_usb_context('@py')
            with patch('sys.platform', 'linux'):
                refresh_linux_usb_context('@ni')
        backend.get_backend.assert_not_called()
        self.assertIs(backend._lib_object, previous)

    def test_discovery_refresh_does_not_close_borrowed_manager(self):
        manager = Mock()
        manager.list_resources.return_value = ('USB0::TEST::INSTR', 'ASRL1::INSTR')
        with patch('pico4824a.bias_scan.power.refresh_linux_usb_context') as refresh:
            self.assertEqual(IT6524DController.resources('@py', manager), ['USB0::TEST::INSTR'])
        refresh.assert_called_once_with('@py')
        manager.close.assert_not_called()

    def test_missing_libusb_reports_error_without_replacing_context(self):
        modules, backend, previous, _ = self.backend()
        backend.get_backend.return_value = None
        with patch('sys.platform', 'linux'), patch.dict('sys.modules', modules):
            with self.assertRaisesRegex(RuntimeError, 'USB'):
                refresh_linux_usb_context('@py')
        self.assertIs(backend._lib_object, previous)

