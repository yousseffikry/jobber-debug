"""
jobber_fsm package initialisation.

Loads environment variables (OPENAI_API_KEY, etc.) **before** any sub-modules
import `openai` or other SDKs, so credentials are present globally.
"""

# ---------------------------------------------------------------------------
# 1. Load .env once, with override=True, via the helper module
# ---------------------------------------------------------------------------

from ._env import *          # noqa  (side-effect: loads .env)

# ---------------------------------------------------------------------------
# 2. Optional package metadata
# ---------------------------------------------------------------------------

__all__ = []                 # populate as needed

try:
    from importlib.metadata import version
    __version__ = version(__name__)
except Exception:
    __version__ = "0.0.0"
