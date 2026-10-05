"""Create a "Deadlock Analyzer" shortcut on the Windows desktop.

Run once:  python make_shortcut.py
The shortcut starts the app with pythonw (no console window) and uses the app's icon.
"""

import os
import subprocess
import sys

from PIL import Image

PROJECT = os.path.dirname(os.path.abspath(__file__))
ICON_PNG = os.path.join(PROJECT, "assets", "icon.png")
ICON_ICO = os.path.join(PROJECT, "assets", "icon.ico")
LAUNCHER = os.path.join(PROJECT, "Deadlock Analyzer.pyw")


def make_ico() -> None:
    """Windows shortcuts need an .ico, which holds several sizes of the same picture."""
    Image.open(ICON_PNG).save(ICON_ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def desktop_folder() -> str:
    # The Desktop can be redirected (e.g. into OneDrive), so ask Windows where it is
    result = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def create_shortcut(path: str) -> None:
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    # PowerShell's WScript.Shell is the standard way to write a .lnk file without extra libraries.
    # Values are passed as environment variables, so paths with spaces or quotes can't break the command.
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); "
              "$s.TargetPath = $env:TARGET; $s.Arguments = '\"' + $env:LAUNCHER + '\"'; "
              "$s.WorkingDirectory = $env:PROJECT; $s.IconLocation = $env:ICON; "
              "$s.Description = 'Deadlock Analyzer'; $s.Save()")
    env = dict(os.environ, LNK=path, TARGET=pythonw, LAUNCHER=LAUNCHER, PROJECT=PROJECT, ICON=ICON_ICO)
    subprocess.run(["powershell", "-NoProfile", "-Command", script], env=env, check=True)


def main():
    make_ico()
    # Not "Deadlock Analyzer.lnk": that's the installed app's shortcut, which installing would overwrite
    # and uninstalling would delete.
    shortcut = os.path.join(desktop_folder(), "Deadlock Analyzer (source).lnk")
    create_shortcut(shortcut)
    print(f"Created {shortcut}")


if __name__ == "__main__":
    main()
