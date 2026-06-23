"""Put the bundle root on sys.path so `import noui_core` works when a script is
run directly (`python scripts/<name>.py`) from anywhere."""

from __future__ import annotations

import sys
from pathlib import Path

_BUNDLE_ROOT = Path(__file__).resolve().parent.parent
if str(_BUNDLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BUNDLE_ROOT))
