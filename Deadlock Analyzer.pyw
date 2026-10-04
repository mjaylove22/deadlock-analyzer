"""Double-click to start Deadlock Analyzer without a terminal window (.pyw runs with pythonw)."""

import os

# screenshots/ and logs/ are relative to the project folder, wherever this was launched from
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app import main  # noqa: E402  (must come after chdir)

main()
