"""Build the Windows installer:  python installer/build.py

1. Copies just the parts of Tesseract the app needs (about 50 MB of its 112 MB) into build/tesseract,
   then checks that the copy can read text on its own, without the full install.
2. Packages the app, Python and that Tesseract with PyInstaller into dist/Deadlock Analyzer/.
   One folder rather than one .exe: a single .exe unpacks itself to a temp folder on every start.
3. Wraps the folder into dist/DeadlockAnalyzer-Setup-<version>.exe with Inno Setup.

Needs: pip install -r requirements-dev.txt, Tesseract (UB Mannheim build, the usual Windows one) and
Inno Setup 6 (winget install JRSoftware.InnoSetup).
"""

import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pefile
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from version import __version__  # noqa: E402

BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")
TESSERACT_COPY = os.path.join(BUILD, "tesseract")
APP_NAME = "Deadlock Analyzer"
TESSERACT_DIRS = [os.environ.get("TESSERACT_DIR", ""), r"C:\Program Files\Tesseract-OCR"]
# Python packages inside the app (requirements.txt and what they pull in), for their licence texts.
# mss ships without a licence file; its MIT notice is in THIRD_PARTY_NOTICES.txt.
PACKAGES = ["customtkinter", "darkdetect", "packaging", "pillow", "pytesseract", "keyboard"]
ISCC_PATHS = [os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
              r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe"]


def find(paths, what):
    for path in paths:
        if path and os.path.exists(path):
            return path
    sys.exit(f"Could not find {what}. Looked in: {', '.join(p for p in paths if p)}")


def imported_dlls(path):
    """The DLLs a Windows program or DLL loads when it starts, from its import table."""
    pe = pefile.PE(path, fast_load=True)
    pe.parse_data_directories([pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
    return [entry.dll.decode() for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])]


def tesseract_files(folder):
    """tesseract.exe plus every DLL in its folder that it needs, directly or through another DLL.
    Windows' own DLLs (KERNEL32.dll etc.) aren't in the folder, so they're left out."""
    in_folder = {name.lower(): name for name in os.listdir(folder)}
    needed, todo = set(), ["tesseract.exe"]
    while todo:
        name = todo.pop().lower()
        if name in needed or name not in in_folder:
            continue
        needed.add(name)
        todo += imported_dlls(os.path.join(folder, in_folder[name]))
    return sorted(in_folder[name] for name in needed)


def tesseract_dir():
    return os.path.dirname(find([os.path.join(d, "tesseract.exe") for d in TESSERACT_DIRS if d], "Tesseract"))


def copy_tesseract():
    source = tesseract_dir()
    shutil.rmtree(TESSERACT_COPY, ignore_errors=True)
    os.makedirs(os.path.join(TESSERACT_COPY, "tessdata"))
    files = tesseract_files(source)
    for name in files:
        shutil.copy2(os.path.join(source, name), TESSERACT_COPY)
    shutil.copy2(os.path.join(source, "tessdata", "eng.traineddata"), os.path.join(TESSERACT_COPY, "tessdata"))
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(TESSERACT_COPY) for f in fs)
    print(f"Tesseract: copied {len(files)} files plus English data, {size / 1e6:.0f} MB")


def check_tesseract():
    """OCR a made-up image with the copy alone: no PATH, no TESSDATA_PREFIX, so the full install can't help."""
    with tempfile.TemporaryDirectory() as temp:
        image = Image.new("L", (600, 120), 255)
        ImageDraw.Draw(image).text((20, 30), "Haze Level 12", fill=0, font=ImageFont.load_default(size=48))
        image.save(os.path.join(temp, "check.png"))
        env = {"SYSTEMROOT": os.environ["SYSTEMROOT"], "PATH": os.path.join(os.environ["SYSTEMROOT"], "System32")}
        result = subprocess.run([os.path.join(TESSERACT_COPY, "tesseract.exe"), "check.png", "stdout"],
                                cwd=temp, env=env, capture_output=True, text=True)
    if "Level" not in result.stdout:
        sys.exit(f"The trimmed Tesseract didn't work on its own:\n{result.stdout}{result.stderr}")
    print(f"Tesseract: the copy works on its own (read {result.stdout.strip()!r})")


def version_file():
    """The .exe's Windows file properties (product name and version), which code signing requires."""
    numbers = tuple(int(n) for n in __version__.split(".")) + (0,)
    strings = {"CompanyName": "mjaylove22", "FileDescription": APP_NAME, "FileVersion": __version__,
               "InternalName": APP_NAME, "OriginalFilename": f"{APP_NAME}.exe", "ProductName": APP_NAME,
               "ProductVersion": __version__, "LegalCopyright": "MIT License"}
    table = ", ".join(f"StringStruct({key!r}, {value!r})" for key, value in strings.items())
    path = os.path.join(BUILD, "version_info.txt")
    with open(path, "w", encoding="utf-8") as f:  # PyInstaller's format: Python syntax, read with eval
        f.write(f"VSVersionInfo(ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}), kids=["
                f"StringFileInfo([StringTable('040904B0', [{table}])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])")
    return path


def run_pyinstaller():
    def data(source, target):
        return ["--add-data", f"{os.path.join(ROOT, source)}{os.pathsep}{target}"]
    subprocess.run([
        sys.executable, "-m", "PyInstaller", os.path.join(ROOT, "Deadlock Analyzer.pyw"),
        "--name", APP_NAME, "--onedir", "--windowed", "--noconfirm", "--clean",
        "--icon", os.path.join(ROOT, "assets", "icon.ico"), "--version-file", version_file(),
        "--collect-data", "customtkinter",  # its themes and fonts
        *data("assets", "assets"),
        "--exclude-module", "PIL.AvifImagePlugin", "--exclude-module", "PIL._avif",  # 8 MB, never used
        "--distpath", DIST, "--workpath", os.path.join(BUILD, "pyinstaller"), "--specpath", BUILD,
    ], check=True)
    folder = os.path.join(DIST, APP_NAME)
    # Tesseract is added afterwards: given to PyInstaller, it also copies every Tesseract DLL a second
    # time next to Python's (50 MB extra). paths.resource() looks in _internal.
    shutil.copytree(TESSERACT_COPY, os.path.join(folder, "_internal", "tesseract"))
    copy_licenses(folder)
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(folder) for f in fs)
    print(f"App folder: {folder} ({size / 1e6:.0f} MB)")


def copy_licenses(folder):
    """The app's licence, THIRD_PARTY_NOTICES.txt, and in licenses/ the licence texts of everything bundled."""
    licenses = os.path.join(folder, "licenses")
    os.makedirs(licenses, exist_ok=True)
    shutil.copy2(os.path.join(ROOT, "LICENSE"), os.path.join(folder, "LICENSE.txt"))
    shutil.copy2(os.path.join(ROOT, "installer", "THIRD_PARTY_NOTICES.txt"), folder)
    shutil.copytree(os.path.join(ROOT, "installer", "licenses"), licenses, dirs_exist_ok=True)  # GPL/LGPL texts
    shutil.copy2(os.path.join(sys.base_prefix, "LICENSE.txt"), os.path.join(licenses, "python.txt"))
    shutil.copy2(os.path.join(tesseract_dir(), "doc", "LICENSE"), os.path.join(licenses, "tesseract.txt"))
    for package in PACKAGES:
        files = [f for f in importlib.metadata.distribution(package).files or []
                 if re.search(r"LICEN[CS]E", f.name, re.I)]
        if not files:
            sys.exit(f"No licence file found for {package}: add its notice to THIRD_PARTY_NOTICES.txt")
        for number, file in enumerate(files):
            shutil.copy2(file.locate(), os.path.join(licenses, f"{package}{'-' + str(number + 1) if number else ''}.txt"))
    print(f"Licences: {len(os.listdir(licenses))} texts in {licenses}")


def run_inno_setup():
    iscc = find(ISCC_PATHS, "Inno Setup 6 (winget install JRSoftware.InnoSetup)")
    subprocess.run([iscc, f"/DAppVersion={__version__}", os.path.join(ROOT, "installer", "setup.iss")], check=True)
    setup = os.path.join(DIST, f"DeadlockAnalyzer-Setup-{__version__}.exe")
    print(f"Installer: {setup} ({os.path.getsize(setup) / 1e6:.0f} MB)")


if __name__ == "__main__":
    copy_tesseract()
    check_tesseract()
    run_pyinstaller()
    run_inno_setup()
