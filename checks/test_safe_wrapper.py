"""Fault injection using an in-memory serial backend; no hardware access."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]


def forbidden_serial(*args, **kwargs):
    raise AssertionError("Tests must never open a real serial backend")


stub = types.ModuleType("serial")
stub.Serial = forbidden_serial
previous = sys.modules.get("serial")
sys.modules["serial"] = stub
try:
    spec = importlib.util.spec_from_file_location("safe_wrapper_under_test", ROOT / "MEMS_serial_device.py")
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
finally:
    if previous is None:
        sys.modules.pop("serial", None)
    else:
        sys.modules["serial"] = previous


class FakeSerial:
    def __init__(self, **kwargs):
        self.is_open = False
        self.port = None
        self.enabled = True  # Represents an already-running controller at connect.
        self.buffer = bytearray()
        self.writes = []
        self.responses = {}
        self.echo = False
        self.gaps = False
        self._gap = False
        self.close_error = None
        self.open_lines = None
        self.short_write = False

    def open(self):
        assert self.port == "SIMULATED_ONLY"
        self.open_lines = (self.dtr, self.rts)
        self.is_open = True

    def close(self):
        if self.close_error:
            raise self.close_error
        self.is_open = False

    def reset_input_buffer(self):
        self.buffer.clear()

    def write(self, packet):
        assert self.is_open
        self.writes.append(packet)
        if self.short_write:
            return len(packet) - 1
        command = packet.decode().strip()
        if command in self.responses:
            response = self.responses[command]
            if isinstance(response, list):
                response = response.pop(0)
            if isinstance(response, BaseException):
                raise response
        elif command == "$MTI$":
            response = b"MTI-Device MTI-MZ-2.4.116:USB Ready in Command Mode\r\n"
        elif command == "MTI+DI":
            self.enabled = False
            response = b"MTI-OK\r\n"
        elif command == "MTI+EN":
            self.enabled = True
            response = b"MTI-OK\r\n"
        elif command == "MTI+GP":
            response = f"MTI-Parameters\r\nMEMSDriverEnable = {int(self.enabled)}\r\n".encode()
        elif command == "MTI+EX":
            response = b"MTI-Device Exit Command Mode\r\n"
        else:
            response = b"MTI-OK\r\n"
        if self.echo:
            self.buffer.extend(packet)
        self.buffer.extend(response)
        return len(packet)

    def read(self, size):
        if self.gaps:
            self._gap = not self._gap
            if self._gap:
                return b""
        if not self.buffer:
            return b""
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result


class SafetyTests(unittest.TestCase):
    def connect(self, responses=None, *, echo=False, gaps=False):
        self.fake = FakeSerial()
        self.fake.responses.update(responses or {})
        self.fake.echo, self.fake.gaps = echo, gaps
        self.dev = wrapper.MEMS_device("SIMULATED_ONLY", verbose=False, sleep_time=0,
                                      response_timeout=0.3, _serial_factory=lambda **kw: self.fake)
        return self.dev

    def configured(self):
        dev = self.connect()
        dev.set_mirror_params(90, 180, 120, verbose=False)
        dev.set_mirror_position(0, 0, verbose=False)
        return dev

    def test_default_backend_is_blocked_in_test_process(self):
        with self.assertRaises(AssertionError):
            wrapper.MEMS_device("COM4", verbose=False)

    def test_startup_disables_previously_enabled_driver_without_motion(self):
        dev = self.connect()
        self.assertEqual(self.fake.writes, [b"$MTI$\n", b"MTI+DI\n", b"MTI+GP\n"])
        self.assertFalse(self.fake.enabled)
        self.assertFalse(dev.is_HV_on)
        self.assertEqual(dev.output_state, "disabled_reported")
        self.assertEqual(self.fake.open_lines, (False, False))
        self.assertEqual(dev.get_mirror_position(False), (None, None))

    def test_unsupported_query_is_honestly_acknowledged_only(self):
        dev = self.connect({"MTI+GP": b"MTI-ERR InvalidCommand\r\n"})
        self.assertEqual(dev.output_state, "disabled_acknowledged")
        self.assertFalse(dev.readback_supported)
        self.assertTrue(dev.exit_safely(False))

    def test_unfamiliar_parameter_format_is_not_guessed(self):
        dev = self.connect({"MTI+GP": b"MTI-Parameters 90 180 120 0\r\n"})
        self.assertIsNone(dev.last_controller_params["driver_enabled"])
        self.assertEqual(dev.output_state, "disabled_acknowledged")

    def test_readback_conflict_rejects_initialization(self):
        with self.assertRaises(wrapper.MEMSError):
            self.connect({"MTI+GP": b"MEMSDriverEnable = 1\r\n"})
        self.assertFalse(self.fake.is_open)
        self.assertNotIn(b"MTI+EN\n", self.fake.writes)

    def test_malformed_readback_rejects_initialization(self):
        for response in [b"MEMSDriverEnable = perhaps\r\n",
                         b"MEMSDriverEnable = 0\r\nMEMSDriverEnable = 1\r\n",
                         b"MEMSDriverEnable = 0\r\nMEMSDriverEnable = perhaps\r\n"]:
            with self.subTest(response=response), self.assertRaises(wrapper.MEMSError):
                self.connect({"MTI+GP": response})

    def test_missing_or_unrecognized_identity_does_not_send_other_commands(self):
        for response in [b"", b"MTI-ERR InvalidCommand\r\n", b"unrelated device\r\n"]:
            with self.subTest(response=response), self.assertRaises(wrapper.MEMSError):
                self.connect({"$MTI$": response})
            self.assertEqual(self.fake.writes, [b"$MTI$\n"])
            self.assertFalse(self.fake.is_open)

    def test_startup_disable_error_does_not_create_successful_connection(self):
        with self.assertRaises(wrapper.MEMSError):
            self.connect({"MTI+DI": b"MTI-ERR InvalidCommand\r\n"})
        self.assertFalse(self.fake.is_open)

    def test_fragmented_and_echoed_responses(self):
        dev = self.connect(echo=True, gaps=True)
        self.assertFalse(dev.is_HV_on)

    def test_device_caches_are_independent(self):
        first = self.configured()
        second = self.connect()
        self.assertIsNone(second.settings["Vbias"])
        self.assertIsNone(second.position["x"])
        self.assertEqual(first.settings["Vbias"], 90)

    def test_enable_requires_explicit_settings_and_initial_position(self):
        dev = self.connect()
        with self.assertRaises(wrapper.MEMSError):
            dev.HV_on(False)
        dev.set_mirror_params(90, 180, 120, False)
        with self.assertRaises(wrapper.MEMSError):
            dev.HV_on(False)
        self.assertNotIn(b"MTI+EN\n", self.fake.writes)

    def test_invalid_parameters_rejected_before_any_parameter_write(self):
        dev = self.connect()
        before = list(self.fake.writes)
        for args in [(90, 180, 49), (101, 180, 120), (90, 201, 120), (80, 180, 120)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                dev.set_mirror_params(*args, verbose=False)
        self.assertEqual(self.fake.writes, before)

    def test_nonfinite_and_out_of_range_positions_rejected(self):
        dev = self.configured()
        before = list(self.fake.writes)
        for x in [float("nan"), float("inf"), -float("inf"), 1.01, -1.01, True]:
            with self.subTest(x=x), self.assertRaises(ValueError):
                dev.set_mirror_position(x, 0, False)
        self.assertEqual(self.fake.writes, before)

    def test_parameter_change_blocked_while_enabled(self):
        dev = self.configured()
        dev.HV_on(False)
        before = list(self.fake.writes)
        with self.assertRaises(wrapper.MEMSError):
            dev.set_Vbias(80, False)
        self.assertEqual(self.fake.writes, before)

    def test_timeout_invalidates_state_and_blocks_motion(self):
        dev = self.configured()
        self.fake.responses["MTI+GT 0.1 0 0"] = b""
        with self.assertRaises(wrapper.MEMSError):
            dev.set_mirror_position(0.1, 0, False)
        self.assertIsNone(dev.is_HV_on)
        before = list(self.fake.writes)
        with self.assertRaises(wrapper.MEMSError):
            dev.set_mirror_position(0, 0, False)
        self.assertEqual(self.fake.writes, before)
        self.assertFalse(dev.exit_safely(False))
        self.assertIn(b"MTI+DI\n", self.fake.writes[len(before):])
        self.assertNotIn(b"MTI+EX\n", self.fake.writes)

    def test_stale_buffered_ok_cannot_mask_a_missing_response(self):
        dev = self.connect()
        self.fake.buffer.extend(b"MTI-OK\r\n")
        self.fake.responses["MTI+VB 90"] = b""
        with self.assertRaises(wrapper.MEMSError):
            dev.set_Vbias(90, False)
        self.assertIsNone(dev.settings["Vbias"])
        self.assertIsNone(dev.is_HV_on)

    def test_partial_ack_is_not_success(self):
        dev = self.connect()
        self.fake.responses["MTI+VB 90"] = b"MTI-OK"
        with self.assertRaises(wrapper.MEMSError):
            dev.set_Vbias(90, False)
        self.assertIsNone(dev.settings["Vbias"])

    def test_disconnection_and_short_write_invalidate_state(self):
        for short in (True, False):
            dev = self.connect()
            self.fake.short_write = short
            if not short:
                self.fake.responses["MTI+VB 90"] = OSError("Disconnected")
            with self.subTest(short=short), self.assertRaises((OSError, wrapper.MEMSError)):
                dev.set_Vbias(90, False)
            self.assertIsNone(dev.is_HV_on)

    def test_shutdown_disables_even_when_already_disabled(self):
        dev = self.connect()
        self.fake.writes.clear()
        self.assertTrue(dev.exit_safely(False))
        self.assertEqual(self.fake.writes, [b"MTI+DI\n", b"MTI+GP\n", b"MTI+EX\n"])
        self.assertFalse(self.fake.is_open)
        self.assertEqual(dev.last_shutdown_report["disable_evidence"], "disabled_reported")

    def test_shutdown_disable_rejection_keeps_connection_and_reports_failure(self):
        dev = self.connect()
        self.fake.responses["MTI+DI"] = b"MTI-ERR InvalidCommand\r\n"
        self.assertFalse(dev.exit_safely(False))
        self.assertTrue(self.fake.is_open)
        self.assertNotIn(b"MTI+EX\n", self.fake.writes)
        self.assertIsNone(dev.is_HV_on)

    def test_shutdown_readback_conflict_is_failure(self):
        dev = self.connect()
        self.fake.responses["MTI+GP"] = b"MEMSDriverEnable = 1\r\n"
        self.assertFalse(dev.exit_safely(False))
        self.assertTrue(self.fake.is_open)

    def test_previously_supported_readback_cannot_silently_disappear(self):
        dev = self.connect()
        self.fake.responses["MTI+GP"] = b"OtherField = 0\r\n"
        self.assertFalse(dev.exit_safely(False))

    def test_failed_parking_still_disables_but_retains_failure(self):
        dev = self.configured()
        dev.HV_on(False)
        self.fake.responses["MTI+GT 0 0 0"] = b"MTI-ERR InvalidCommandData\r\n"
        self.assertFalse(dev.exit_safely(False, park=True))
        self.assertFalse(self.fake.enabled)
        self.assertFalse(self.fake.is_open)
        self.assertIn("Parking failed", dev.last_shutdown_report["errors"][0])

    def test_parking_interrupt_still_attempts_disable(self):
        dev = self.configured()
        dev.HV_on(False)
        def interrupt(*args, **kwargs):
            raise KeyboardInterrupt()
        dev.set_mirror_position = interrupt
        with self.assertRaises(KeyboardInterrupt):
            dev.exit_safely(False, park=True)
        self.assertFalse(self.fake.enabled)

    def test_failed_enable_attempts_disable(self):
        dev = self.configured()
        self.fake.responses["MTI+EN"] = b"MTI-ERR InvalidCommand\r\n"
        self.fake.writes.clear()
        with self.assertRaises(wrapper.MEMSError):
            dev.HV_on(False)
        self.assertEqual(self.fake.writes[:2], [b"MTI+EN\n", b"MTI+DI\n"])

    def test_logout_and_close_failure_not_reported_as_success(self):
        for close_failure in (False, True):
            dev = self.connect()
            if close_failure:
                self.fake.close_error = OSError("Cannot close")
            else:
                self.fake.responses["MTI+EX"] = b"MTI-ERR InvalidCommand\r\n"
            self.assertFalse(dev.exit_safely(False))

    def test_context_manager_preserves_body_exception_and_cleans_up(self):
        dev = self.connect()
        for error in (ValueError("body error"), KeyboardInterrupt()):
            dev = self.connect()
            with self.assertRaises(type(error)):
                with dev:
                    raise error
            self.assertFalse(self.fake.enabled)
            self.assertFalse(self.fake.is_open)

    def test_context_manager_exposes_shutdown_failure(self):
        dev = self.connect()
        self.fake.responses["MTI+DI"] = b"MTI-ERR InvalidCommand\r\n"
        with self.assertRaises(wrapper.MEMSShutdownError):
            with dev:
                pass

    def test_close_does_not_claim_to_disable(self):
        dev = self.configured()
        dev.HV_on(False)
        before = list(self.fake.writes)
        dev.close()
        self.assertEqual(self.fake.writes, before)
        self.assertTrue(self.fake.enabled)
        self.assertIsNone(dev.is_HV_on)


if __name__ == "__main__":
    unittest.main(verbosity=2)
