"""Regression for real PyVISA managers without __enter__/__exit__. No device I/O."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from pico4824a.bias_scan.power import IT6524DController
from pico4824a.web import WebControlState

USB='USB0::0x2EC7::0x6522::800657011776910005::INSTR'


class ManagerWithoutContextProtocol:
    def __init__(self, failure=None):
        self.failure=failure
        self.closed=0
    def list_resources(self):
        if self.failure: raise self.failure
        return (USB, 'ASRL1::INSTR')
    def close(self): self.closed+=1


class VisaResourceTests(unittest.TestCase):
    def test_enumeration_without_context_manager(self):
        manager=ManagerWithoutContextProtocol()
        factory=Mock(return_value=manager)
        with patch.dict('sys.modules', {'pyvisa':SimpleNamespace(ResourceManager=factory)}):
            self.assertEqual(IT6524DController.resources(),[USB])
        factory.assert_called_once_with('')
        self.assertEqual(manager.closed,1)

    def test_enumeration_failure_still_closes_owned_manager(self):
        manager=ManagerWithoutContextProtocol(OSError('enumeration failed'))
        with patch.dict('sys.modules', {'pyvisa':SimpleNamespace(ResourceManager=lambda _:manager)}):
            with self.assertRaisesRegex(RuntimeError,'enumeration failed'):
                IT6524DController.resources()
        self.assertEqual(manager.closed,1)

    def test_existing_connection_manager_is_not_closed(self):
        manager=ManagerWithoutContextProtocol()
        self.assertEqual(IT6524DController.resources(manager=manager),[USB])
        self.assertEqual(manager.closed,0)

    def test_failed_existing_manager_is_not_closed(self):
        manager=ManagerWithoutContextProtocol(OSError('USB disconnected'))
        with self.assertRaisesRegex(RuntimeError,'USB disconnected'):
            IT6524DController.resources(manager=manager)
        self.assertEqual(manager.closed,0)

    def test_web_reuses_connected_manager(self):
        control=WebControlState();manager=ManagerWithoutContextProtocol()
        control.bias_power=SimpleNamespace(manager=manager)
        try:
            self.assertEqual(control.bias_resources(),[USB])
            self.assertEqual(manager.closed,0)
        finally:
            control.bias_power=None;control.shutdown()

    def test_web_blocks_enumeration_during_instrument_task(self):
        control=WebControlState()
        try:
            for state in ('running','paused'):
                control.status.state=state
                with self.assertRaisesRegex(RuntimeError,'正在运行'):control.bias_resources()
            control.status.state='idle';control.bias_cleanup_pending=True
            with self.assertRaisesRegex(RuntimeError,'正在运行'):control.bias_resources()
        finally:
            control.status.state='idle';control.bias_cleanup_pending=False;control.shutdown()

if __name__=='__main__':unittest.main()
