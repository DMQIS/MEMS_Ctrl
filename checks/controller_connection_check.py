"""Opt-in hardware check. Only login, disable, parameter query and logout.

Does not set mirror parameters, command position, or enable HV. Opening a port
and disabling the driver are active operations. Run only for that purpose.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Explicitly allow the hardware check")
    parser.add_argument("--port")
    parser.add_argument("--usb-serial")
    args = parser.parse_args()
    if not args.run or not args.port or not args.usb_serial:
        parser.error("Hardware access requires --run, --port, and --usb-serial")

    import serial
    from serial.tools import list_ports
    from MEMS_serial_device import MEMS_device

    matches = [p for p in list_ports.comports()
               if p.device.casefold() == args.port.casefold()
               and p.serial_number == args.usb_serial and p.vid == 0x0403 and p.pid == 0x6001]
    if len(matches) != 1:
        raise SystemExit("Expected FTDI interface not uniquely present; no port opened")

    class RestrictedSerial(serial.Serial):
        def write(self, data):
            if data not in (b"$MTI$\n", b"MTI+DI\n", b"MTI+GP\n", b"MTI+EX\n"):
                raise RuntimeError("Hardware check blocked a command outside its allowlist")
            return super().write(data)

    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "LIVE_COMMUNICATION_AND_DISABLE_ONLY",
        "port": args.port,
        "usb_serial": args.usb_serial,
        "wrapper_sha256": hashlib.sha256((ROOT / "MEMS_serial_device.py").read_bytes()).hexdigest(),
        "success": False,
        "output_voltage_measured": False,
        "mirror_motion_tested": False,
    }
    dev = None
    try:
        dev = MEMS_device(args.port, verbose=False, response_timeout=3,
                          _serial_factory=RestrictedSerial)
        report.update(identity=dev.identity, output_state_after_connect=dev.output_state,
                      state_evidence=dev.state_evidence, controller_parameters=dev.last_controller_params)
        report["success"] = dev.exit_safely(verbose=False)
    except BaseException as exc:
        dev = dev or getattr(exc, "device", None)
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["error_notes"] = getattr(exc, "__notes__", [])
        if dev is not None and dev.identity is not None and dev.ser.is_open:
            try:
                dev.exit_safely(verbose=False)
            except BaseException as cleanup_error:
                report["cleanup_error"] = str(cleanup_error)
    finally:
        if dev is not None:
            report["transcript"] = dev.transcript
            report["shutdown"] = dev.last_shutdown_report
            if dev.ser.is_open:
                report["port_release_note"] = "Diagnostic ended with incomplete shutdown; releasing the port does not establish disabled output."
                try:
                    dev.close()
                except Exception as close_error:
                    report["port_close_error"] = str(close_error)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = ROOT / "checks" / f"controller_connection_{stamp}.json"
        destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        print("Saved:", destination)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
