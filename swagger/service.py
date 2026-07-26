"""Install and manage the local macOS Swagger Engine LaunchAgent."""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
from pathlib import Path


LABEL = "com.shaunkissing.swagger-engine"


def _paths() -> tuple[Path, Path, Path]:
    root = Path(__file__).resolve().parent.parent
    python = root / ".venv" / "bin" / "python"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    return root, python, plist


def _payload(root: Path, python: Path) -> dict[str, object]:
    state = root / "swagger_state"
    state.mkdir(parents=True, exist_ok=True)
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(python),
            "-m",
            "swagger.engine",
            "--health-port",
            "8080",
        ],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        # Restart crashes and fail-closed dependency halts. A graceful manual
        # stop or a kill-switch stop exits successfully and stays stopped.
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 60,
        "ProcessType": "Background",
        "StandardOutPath": str(state / "service.stdout.log"),
        "StandardErrorPath": str(state / "service.stderr.log"),
    }


def _launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/launchctl", *args],
        check=check,
        text=True,
        capture_output=True,
    )


def install() -> int:
    root, python, plist = _paths()
    if not python.exists():
        print(f"virtual-environment Python is missing: {python}")
        return 2
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps(_payload(root, python), sort_keys=True))
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", domain, str(plist), check=False)
    result = _launchctl("bootstrap", domain, str(plist), check=False)
    if result.returncode:
        print(result.stderr.strip() or result.stdout.strip())
        return result.returncode
    print(f"installed and started {LABEL}")
    print(f"plist: {plist}")
    return 0


def uninstall() -> int:
    _, _, plist = _paths()
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", domain, str(plist), check=False)
    if plist.exists():
        plist.unlink()
    print(f"uninstalled {LABEL}")
    return 0


def status() -> int:
    result = _launchctl(
        "print", f"gui/{os.getuid()}/{LABEL}", check=False
    )
    if result.returncode:
        print(f"{LABEL} is not loaded")
        return 1
    # launchctl output includes no Swagger credentials because the plist
    # deliberately contains no environment variables.
    print(result.stdout)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "status", "uninstall"))
    args = parser.parse_args()
    return {"install": install, "status": status, "uninstall": uninstall}[
        args.command
    ]()


if __name__ == "__main__":
    raise SystemExit(main())
