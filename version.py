"""The app's version, shown in the app and used by the installer."""

__version__ = "0.2.6"

LATEST_RELEASE_API = "https://api.github.com/repos/mjaylove22/deadlock-analyzer/releases/latest"
# Each release also carries the installer under this fixed name, so the link never changes
DOWNLOAD_URL = "https://github.com/mjaylove22/deadlock-analyzer/releases/latest/download/DeadlockAnalyzer-Setup.exe"


def is_newer(tag: str, current: str = __version__) -> bool:
    """Whether a release tag like "v0.2.1" is a later version than current ("0.2.0")."""
    def parts(version):
        return tuple(int(part) for part in version.strip().lstrip("v").split("."))
    try:
        return parts(tag) > parts(current)
    except ValueError:
        return False  # not a plain version number: never nag about it
