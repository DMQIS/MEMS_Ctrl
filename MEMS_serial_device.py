"""Mirrorcle control: identify, disable on connect, and retain state evidence.

Importing this module does not open a port. Construction actively identifies and
disables the controller. Acknowledgments/readback are not voltage measurements.
"""
import math
import re
import threading
from functools import wraps
from numbers import Real
from time import monotonic, sleep

import serial


class MEMSError(RuntimeError):
    """Operation could not be established as successful."""


class MEMSCommunicationError(MEMSError):
    pass


class MEMSShutdownError(MEMSError):
    pass


def _locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class MEMS_device:
    def __init__(self, port, timeout=0, sleep_time=0.2, verbose=True,
                 *, response_timeout=None, _serial_factory=None):
        self._lock = threading.RLock()
        self.response_timeout = response_timeout if response_timeout is not None else (timeout or 2.0)
        if not math.isfinite(self.response_timeout) or self.response_timeout <= 0:
            raise ValueError("response_timeout must be finite and positive")
        if not math.isfinite(sleep_time) or sleep_time < 0:
            raise ValueError("sleep_time must be finite and nonnegative")
        self.sleep_time = sleep_time
        self._quiet_time = max(0.02, sleep_time)
        self.settings = dict(Vbias=None, VdifferenceMax=None, HardwareFilterBW=None)
        self.position = dict(x=None, y=None)
        self.output_state = "unknown"
        self.state_evidence = "No controller response yet"
        self.identity = None
        self.transcript = []
        self.last_controller_params = None
        self.readback_supported = None
        self.last_shutdown_report = None
        self._faulted = False
        self._ambiguous_reply = False
        self._shutdown_complete = False
        factory = _serial_factory or serial.Serial
        # Configure DTR/RTS before opening; OS/adapter behavior can still vary.
        self.ser = factory(port=None, baudrate=115200, timeout=min(0.05, self.response_timeout),
                           write_timeout=self.response_timeout, rtscts=False,
                           xonxoff=True, dsrdtr=False)
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.port = port
        try:
            self.ser.open()
            self.identity = self._exchange("$MTI$", kind="identity")
            self.HV_off(verbose=False)
        except BaseException as exc:
            if self.identity is not None and self.ser.is_open:
                try:
                    self.HV_off(verbose=False)
                except BaseException as cleanup_error:
                    exc.add_note("Startup disable retry failed: " + str(cleanup_error))
            try:
                self.close()
            except Exception as close_error:
                exc.add_note("Port close failed: " + str(close_error))
            exc.add_note("Initialization failed. Output state must not be assumed off.")
            exc.device = self
            raise
        if verbose:
            print(self)

    @property
    def is_HV_on(self):
        """True, False, or None (unknown), based on protocol evidence only."""
        if self.output_state.startswith("enabled"):
            return True
        if self.output_state.startswith("disabled"):
            return False
        return None

    def __str__(self):
        return (f"MEMS controller: {self.identity or 'unidentified'}\n"
                f"Output: {self.output_state}; {self.state_evidence}\n"
                f"Cached settings: {self.settings}\n"
                f"Last commanded position (not measured): {self.position}")

    def _unknown(self, reason, *, ambiguous=False):
        self.output_state = "unknown"
        self.state_evidence = reason
        self._faulted = True
        self._ambiguous_reply = self._ambiguous_reply or ambiguous

    def _require_ready(self):
        if not self.ser.is_open or self._faulted or self.is_HV_on is None:
            raise MEMSError("Output/communication state is uncertain; motion and settings are blocked. Reconnect to establish state.")

    def _exchange(self, command, *, kind="ack", emergency=False):
        """Complete lines and bounded waits, with one command outstanding.

        There are no transaction IDs. After a timeout, a late generic OK cannot
        prove that an emergency disable succeeded. Reconnect before proceeding.
        """
        command = command.rstrip("\r\n")
        if not command or "\n" in command or "\r" in command:
            raise ValueError("Exactly one nonempty serial command is required")
        if not self.ser.is_open:
            self._unknown("Serial port is closed", ambiguous=True)
            raise MEMSCommunicationError(self.state_evidence)
        if self._faulted and not emergency:
            raise MEMSCommunicationError("Communication is faulted; only a disable attempt is allowed")
        entry = {"command": command, "response": "", "outcome": "pending"}
        self.transcript.append(entry)
        buffer, received, lines = bytearray(), bytearray(), []
        started = last_byte = monotonic()
        try:
            self.ser.reset_input_buffer()
            packet = command.encode("ascii") + b"\n"
            if self.ser.write(packet) != len(packet):
                raise MEMSCommunicationError("Incomplete serial write")
            while monotonic() - started < self.response_timeout:
                chunk = self.ser.read(1)
                if chunk:
                    last_byte = monotonic()
                    received.extend(chunk)
                    buffer.extend(chunk)
                    if len(received) > 16384:
                        raise MEMSCommunicationError("Response exceeded 16 KiB")
                    if chunk != b"\n":
                        continue
                    line = buffer.decode("ascii").strip()
                    buffer.clear()
                    if not line or line == command:
                        continue
                    lines.append(line)
                    if line.startswith("MTI-ERR"):
                        if kind == "params" and line == "MTI-ERR InvalidCommand":
                            entry["outcome"] = "unsupported"
                            return None
                        self._unknown("Controller rejected " + command + ": " + line)
                        entry["outcome"] = "rejected"
                        raise MEMSCommunicationError(self.state_evidence)
                    if kind == "identity":
                        if re.fullmatch(r"MTI-Device MTI-[^\r\n]+Ready in [Cc]ommand [Mm]ode", line):
                            entry["outcome"] = "success"
                            return line
                        raise MEMSCommunicationError("Unrecognized controller identity: " + line)
                    if kind == "ack":
                        if line != "MTI-OK":
                            raise MEMSCommunicationError("Unexpected acknowledgment: " + line)
                        entry["outcome"] = "success"
                        return line
                    if kind == "exit":
                        if line.lower() != "mti-device exit command mode":
                            raise MEMSCommunicationError("Unexpected logout response: " + line)
                        entry["outcome"] = "success"
                        return line
                elif kind == "params" and lines and not buffer and monotonic() - last_byte >= self._quiet_time:
                    if lines == ["MTI-OK"]:
                        raise MEMSCommunicationError("Parameter query returned only an acknowledgment")
                    entry["outcome"] = "success"
                    return "\n".join(lines)
                else:
                    sleep(0.001)
            raise MEMSCommunicationError("Timed out waiting for a complete response to " + command)
        except BaseException as exc:
            if entry["outcome"] != "rejected":
                self._unknown(str(exc) or type(exc).__name__, ambiguous=True)
                entry["outcome"] = "uncertain"
            raise
        finally:
            entry["response"] = received.decode("ascii", errors="replace")

    @_locked
    def read_controller_params(self):
        """Fresh GP response; unfamiliar firmware formats remain raw.

        Only the explicit MEMSDriverEnable key is parsed, never inferred from
        unnamed numbers. Status readback is not measured output voltage.
        """
        raw = self._exchange("MTI+GP", kind="params",
                             emergency=self._faulted and not self._ambiguous_reply)
        values = [] if raw is None else re.findall(
            r"^\s*(?:MTI[-:]\s*)?MEMSDriverEnable\s*[:=]\s*(true|false|0|1)\s*$",
            raw, flags=re.IGNORECASE | re.MULTILINE)
        enabled = None
        field_count = len(re.findall(r"\bMEMSDriverEnable\b", raw or "", re.IGNORECASE))
        if len(values) == 1 and field_count == 1:
            enabled = values[0].lower() in ("true", "1")
        elif values or field_count:
            self._unknown("Malformed or duplicate MEMSDriverEnable readback")
            raise MEMSCommunicationError(self.state_evidence)
        elif self.readback_supported is True:
            self._unknown("Previously available output-state readback disappeared")
            raise MEMSCommunicationError(self.state_evidence)
        self.readback_supported = enabled is not None
        self.last_controller_params = {"raw": raw, "driver_enabled": enabled}
        if enabled is not None:
            self.output_state = "enabled_reported" if enabled else "disabled_reported"
            self.state_evidence = "Controller parameter readback; output voltage not measured"
        return dict(self.last_controller_params)

    def _set_output(self, enabled):
        self._exchange("MTI+EN" if enabled else "MTI+DI", emergency=not enabled)
        if self._ambiguous_reply:
            self._unknown("Disable attempted after uncertain communication; a generic OK cannot confirm its outcome")
            raise MEMSCommunicationError(self.state_evidence)
        self.output_state = "enabled_acknowledged" if enabled else "disabled_acknowledged"
        self.state_evidence = "Command acknowledged; independent state readback unavailable"
        # An explicit error leaves no outstanding reply. Disable may establish
        # state, but other operations stay blocked until a new connection.
        result = self.read_controller_params()
        if result["driver_enabled"] is not None and result["driver_enabled"] != enabled:
            self._unknown("Controller readback disagrees with the requested output state")
            raise MEMSCommunicationError(self.state_evidence)
        return True

    @_locked
    def HV_off(self, verbose=True):
        """Always attempt disable, regardless of the last recorded state."""
        result = self._set_output(False)
        if verbose:
            print(self.output_state + ": " + self.state_evidence)
        return result

    @_locked
    def HV_on(self, verbose=True):
        self._require_ready()
        if any(v is None for v in self.settings.values()):
            raise MEMSError("Set all three mirror parameters before enabling")
        if any(v is None for v in self.position.values()):
            raise MEMSError("Explicitly set an initial position while disabled before enabling")
        if self.settings["VdifferenceMax"] > 2 * self.settings["Vbias"]:
            raise MEMSError("VdifferenceMax must not exceed twice Vbias")
        try:
            result = self._set_output(True)
        except BaseException as exc:
            try:
                self.HV_off(verbose=False)
            except BaseException as cleanup_error:
                exc.add_note("Disable after enable failure failed: " + str(cleanup_error))
            raise
        if verbose:
            print(self.output_state + ": " + self.state_evidence)
        return result

    @staticmethod
    def _valid_integer(value, low, high):
        return type(value) is int and low <= value <= high

    def _set_parameter(self, name, command, value, low, high, verbose):
        self._require_ready()
        if self.is_HV_on is not False:
            raise MEMSError("Parameters may only be changed with the driver disabled")
        if not self._valid_integer(value, low, high):
            raise ValueError(f"{name} must be an integer in [{low}, {high}]")
        self._exchange(f"MTI+{command} {value}")
        self.settings[name] = value
        if verbose:
            print(f"{name} = {value} (acknowledged)")
        return True

    @_locked
    def set_Vbias(self, Vbias, verbose=True):
        return self._set_parameter("Vbias", "VB", Vbias, 0, 100, verbose)

    @_locked
    def set_VdifferenceMax(self, VdifferenceMax, verbose=True, safety=True):
        # Retain the legacy keyword; safety checks cannot be disabled.
        return self._set_parameter("VdifferenceMax", "VD", VdifferenceMax, 0, 200, verbose)

    @_locked
    def set_HardwareFilterBW(self, HardwareFilterBW, verbose=True):
        return self._set_parameter("HardwareFilterBW", "BW", HardwareFilterBW, 50, 15000, verbose)

    @_locked
    def set_mirror_params(self, Vbias, VdifferenceMax, HardwareFilterBW, verbose=True):
        for name, value, low, high in [("Vbias", Vbias, 0, 100),
                                     ("VdifferenceMax", VdifferenceMax, 0, 200),
                                     ("HardwareFilterBW", HardwareFilterBW, 50, 15000)]:
            if not self._valid_integer(value, low, high):
                raise ValueError(f"{name} must be an integer in [{low}, {high}]")
        if VdifferenceMax > 2 * Vbias:
            raise ValueError("VdifferenceMax must not exceed twice Vbias")
        self.set_Vbias(Vbias, verbose)
        self.set_VdifferenceMax(VdifferenceMax, verbose)
        self.set_HardwareFilterBW(HardwareFilterBW, verbose)
        return True

    @_locked
    def get_mirror_params(self, verbose=True):
        """Legacy API: cached acknowledged settings, not fresh readback."""
        if verbose:
            print("Cached settings, not fresh readback:", self.settings)
        return self.settings["Vbias"], self.settings["VdifferenceMax"], self.settings["HardwareFilterBW"]

    @_locked
    def set_mirror_position(self, x, y, verbose=True):
        self._require_ready()
        if any(v is None for v in self.settings.values()):
            raise MEMSError("Set mirror parameters before issuing a position command")
        if not all(isinstance(v, Real) and not isinstance(v, bool) and math.isfinite(v) and -1 <= v <= 1 for v in (x, y)):
            raise ValueError("X and Y must be finite numbers in [-1, 1]")
        self._exchange(f"MTI+GT {x} {y} 0")
        self.position.update(x=x, y=y)
        if verbose:
            print(f"Commanded position ({x}, {y}); physical position not measured")
        return True

    @_locked
    def get_mirror_position(self, verbose=True):
        """Last acknowledged command, not a position measurement."""
        if verbose:
            print("Last commanded position, not measured:", self.position)
        return self.position["x"], self.position["y"]

    @_locked
    def troubleshoot(self):
        self._require_ready()
        self._exchange("MTI+EC")
        return True

    @_locked
    def exit_safely(self, verbose=True, *, park=False):
        """Disable then logout/close. Failed disable keeps the port open.

        Parking is opt-in. Its failure still attempts disable and remains a
        failure in the final report. Interruptions are re-raised after cleanup.
        """
        if self._shutdown_complete:
            return True
        errors, interrupted, disabled = [], None, False
        try:
            if park:
                self._require_ready()
                if self.is_HV_on is True:
                    self.set_mirror_position(0, 0, verbose=False)
        except BaseException as exc:
            errors.append("Parking failed: " + str(exc))
            if not isinstance(exc, Exception):
                interrupted = exc
        finally:
            try:
                disabled = self.HV_off(verbose=False)
            except BaseException as exc:
                errors.append("Disable failed: " + str(exc))
                if not isinstance(exc, Exception):
                    interrupted = interrupted or exc
        evidence = self.output_state
        if disabled:
            try:
                self._exchange("MTI+EX", kind="exit", emergency=True)
                self.close()
            except BaseException as exc:
                errors.append("Logout/close failed: " + str(exc))
                if not isinstance(exc, Exception):
                    interrupted = interrupted or exc
        self.last_shutdown_report = {
            "success": disabled and not errors and not self.ser.is_open,
            "disable_confirmed_at_protocol_level": disabled,
            "disable_evidence": evidence,
            "port_closed": not self.ser.is_open,
            "errors": errors,
            "output_voltage_measured": False,
        }
        self._shutdown_complete = self.last_shutdown_report["success"]
        if verbose:
            print(self.last_shutdown_report)
        if interrupted is not None:
            raise interrupted
        return self._shutdown_complete

    @_locked
    def close(self):
        """Release the serial port only; this does not disable hardware."""
        try:
            self.ser.close()
        finally:
            self._unknown("Port closed; current output state is not being observed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            ok = self.exit_safely(verbose=False)
        except BaseException as cleanup_error:
            if exc is None:
                raise
            exc.add_note("MEMS cleanup failed: " + str(cleanup_error))
            return False
        if not ok:
            message = "MEMS shutdown incomplete: " + str(self.last_shutdown_report)
            if exc is not None:
                exc.add_note(message)
            else:
                raise MEMSShutdownError(message)
        return False
