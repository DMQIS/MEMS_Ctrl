"""Run simulated safety regression tests and save a source-hashed report."""
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed.append(test.id())


def main():
    suite = unittest.defaultTestLoader.discover(str(ROOT / "checks"), pattern="test_safe_wrapper.py")
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult).run(suite)
    try:
        version = importlib.metadata.version("pyserial")
    except importlib.metadata.PackageNotFoundError:
        version = None
    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "OFFLINE_SIMULATION_ONLY",
        "real_serial_connections_opened": 0,
        "hardware_commands_sent": 0,
        "wrapper_sha256": hashlib.sha256((ROOT / "MEMS_serial_device.py").read_bytes()).hexdigest(),
        "test_source_sha256": hashlib.sha256((ROOT / "checks" / "test_safe_wrapper.py").read_bytes()).hexdigest(),
        "python_executable": sys.executable,
        "pyserial_version": version,
        "tests_run": result.testsRun,
        "success": result.wasSuccessful(),
        "passed": result.passed,
        "failures": [(str(test), detail) for test, detail in result.failures],
        "errors": [(str(test), detail) for test, detail in result.errors],
        "limitations": "Synthetic replies validate software paths only; they do not establish firmware behavior, physical voltage, or mirror motion.",
    }
    destination = ROOT / "checks" / "safe_wrapper_results.json"
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Saved:", destination)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
