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
    """Return Windows REAL Desktop path (handles OneDrive redirect properly).

    Uses Windows Shell API via pywin32 if available — this returns the
    actual Desktop the user sees, not the orphaned C:\\Users\\X\\Desktop
    folder that Windows leaves behind when OneDrive Desktop redirect is on.

    Falls back to manual detection if pywin32 not available.
    """
    # PREFERRED: ask Windows directly for the Desktop CSIDL/known folder.
    try:
        from win32com.shell import shell, shellcon
        path = shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0)
        if path and Path(path).exists():
            return Path(path)
    except ImportError:
        pass
    except Exception:
        pass

    # FALLBACK 1: read from Windows registry (User Shell Folders -> Desktop)
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as k:
            value, _ = winreg.QueryValueEx(k, "Desktop")
            expanded = os.path.expandvars(value)
            if Path(expanded).exists():
                return Path(expanded)
    except Exception:
        pass

    # FALLBACK 2: scan candidate locations (OneDrive variations + standard)
    profile = os.environ.get("USERPROFILE", "")
    if profile:
        # Try OneDrive variants FIRST (they're often the active Desktop)
        for d in Path(profile).glob("OneDrive*"):
            for sub in ("Desktop", "Bureau"):
                candidate = d / sub
                if candidate.exists():
                    return candidate
        # Plain standard Desktop / Bureau last
        for sub in ("Desktop", "Bureau"):
            candidate = Path(profile) / sub
            if candidate.exists():
                return candidate

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
