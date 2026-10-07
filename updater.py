"""One-click updates: find the newest release, download its installer, check it against the SHA-256
GitHub records for the file, then run it. The installer replaces the app's files (settings, notes and
cache are kept) and, when started with /relaunch=1, opens the app again.

Never silent: it only runs when the user clicks Update now, and never while Deadlock is running. The
checksum proves the file is the one GitHub has for the release (no broken or swapped download); it
can't prove who uploaded it, which is why a person still decides when to install.
"""

import hashlib
import json
import logging
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional

from version import LATEST_RELEASE_API

logger = logging.getLogger(__name__)
SETUP_NAME = "DeadlockAnalyzer-Setup.exe"  # the release file with the permanent name
TRUSTED_HOSTS = ("github.com", "githubusercontent.com")  # release downloads redirect to GitHub's file servers
CHUNK = 256 * 1024


def get(url: str):
    if urllib.parse.urlparse(url).scheme != "https":
        raise ValueError(f"not an https address: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "deadlock-analyzer (learning project)"})
    response = urllib.request.urlopen(request, timeout=30)
    host = urllib.parse.urlparse(response.geturl()).hostname or ""
    if not any(host == h or host.endswith("." + h) for h in TRUSTED_HOSTS):  # after any redirects
        response.close()
        raise ValueError(f"the download came from {host}, not GitHub")
    return response


def release_info(data: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """The newest release as {"tag", "url", "sha256"} from GitHub's release JSON. url and sha256 are None
    when it has no installer with a recorded checksum: then the app only offers the download page."""
    asset = next((a for a in data.get("assets", []) if a.get("name") == SETUP_NAME), None)
    digest = (asset or {}).get("digest") or ""
    if not asset or not digest.startswith("sha256:"):
        return {"tag": data["tag_name"], "url": None, "sha256": None}
    return {"tag": data["tag_name"], "url": asset["browser_download_url"], "sha256": digest.split(":", 1)[1].lower()}


def latest_release() -> Dict[str, Optional[str]]:
    with get(LATEST_RELEASE_API) as response:
        return release_info(json.loads(response.read().decode("utf-8")))


def download(release: Dict[str, str], progress: Callable[[int, int], None] = lambda done, total: None,
             folder: Optional[str] = None) -> str:
    """Download the release's installer to a temp folder and return its path, or raise ValueError when its
    SHA-256 doesn't match (the file is deleted then). progress(bytes so far, total bytes or 0)."""
    folder = folder or os.path.join(tempfile.gettempdir(), "DeadlockAnalyzer-update")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"DeadlockAnalyzer-Setup-{release['tag']}.exe")
    digest, done = hashlib.sha256(), 0
    with get(release["url"]) as response, open(path + ".part", "wb") as f:
        total = int(response.headers.get("Content-Length") or 0)
        while chunk := response.read(CHUNK):
            f.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            progress(done, total)
    if digest.hexdigest() != release["sha256"]:
        os.remove(path + ".part")
        raise ValueError("the download didn't match the checksum GitHub has for it, so it wasn't run")
    os.replace(path + ".part", path)
    return path


def install(path: str) -> None:
    """Start the installer on its own (it outlives this app, which must close right after so its files
    can be replaced). /SILENT shows only a progress bar; /relaunch=1 makes it open the app when done."""
    logger.info(f"Starting the update: {path}")
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/relaunch=1"],
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
