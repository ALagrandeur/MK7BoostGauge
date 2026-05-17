"""Create a Windows desktop shortcut that launches MK7BoostGauge.

Run once:
    python pc/install_shortcut.py

Creates 'MK7BoostGauge.lnk' on your Desktop pointing to pc/launch.py.
Double-click the shortcut to start the UI.

Uses pywin32 (preferred) or falls back to PowerShell COM if pywin32 missing.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def get_desktop_path() -> Path:
    """Return Windows Desktop path (works with OneDrive redirects too)."""
    # Try USERPROFILE\Desktop first (standard)
    profile = os.environ.get("USERPROFILE", "")
    if profile:
        desktop = Path(profile) / "Desktop"
        if desktop.exists():
            return desktop
        # OneDrive Desktop redirect (common on Windows 11)
        onedrive = Path(profile) / "OneDrive" / "Desktop"
        if onedrive.exists():
            return onedrive
        onedrive_alt = Path(profile) / "OneDrive - ÉNERSERV INC" / "Bureau"
        if onedrive_alt.exists():
            return onedrive_alt
    raise RuntimeError("Could not find Desktop folder")


def create_shortcut_pywin32(target_py: Path, shortcut_path: Path, working_dir: Path) -> None:
    """Use pywin32 to create the .lnk (preferred method)."""
    from win32com.client import Dispatch

    shell = Dispatch("WScript.Shell")
    sc = shell.CreateShortCut(str(shortcut_path))
    sc.TargetPath = sys.executable  # python.exe
    sc.Arguments = f'"{target_py}"'
    sc.WorkingDirectory = str(working_dir)
    sc.IconLocation = sys.executable + ",0"
    sc.WindowStyle = 7  # minimized (background, just Flask + browser)
    sc.Description = "MK7BoostGauge v3 - PC UI"
    sc.save()


def create_shortcut_powershell(target_py: Path, shortcut_path: Path, working_dir: Path) -> None:
    """Fallback: use PowerShell COM to create .lnk if pywin32 missing."""
    ps_script = f'''
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut("{shortcut_path}")
$sc.TargetPath = "{sys.executable}"
$sc.Arguments = '"{target_py}"'
$sc.WorkingDirectory = "{working_dir}"
$sc.IconLocation = "{sys.executable},0"
$sc.WindowStyle = 7
$sc.Description = "MK7BoostGauge v3 - PC UI"
$sc.Save()
'''
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        check=True, capture_output=True, text=True
    )


def main() -> int:
    here = Path(__file__).parent.resolve()
    project_root = here.parent
    launch_py = here / "launch.py"

    if not launch_py.exists():
        print(f"ERROR: {launch_py} not found")
        return 1

    desktop = get_desktop_path()
    shortcut_path = desktop / "MK7BoostGauge.lnk"

    print(f"Creating shortcut at: {shortcut_path}")
    print(f"Target: python {launch_py}")
    print(f"Working dir: {project_root}")

    try:
        # Try pywin32 first
        import win32com.client  # noqa: F401
        create_shortcut_pywin32(launch_py, shortcut_path, project_root)
        print("OK - shortcut created (pywin32)")
    except ImportError:
        print("pywin32 not installed, using PowerShell fallback...")
        try:
            create_shortcut_powershell(launch_py, shortcut_path, project_root)
            print("OK - shortcut created (PowerShell)")
        except subprocess.CalledProcessError as e:
            print(f"ERROR PowerShell failed: {e.stderr}")
            return 1

    print()
    print(f"Done! Double-click '{shortcut_path.name}' on your Desktop to launch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
