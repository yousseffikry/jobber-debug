"""Long-term-memory helpers for Jobber-FSM."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from jobber_fsm.config.config import USER_PREFERENCES_PATH
from jobber_fsm.utils.logger import logger

# --------------------------------------------------------------------------- #
#  User preference helpers
# --------------------------------------------------------------------------- #

def get_user_ltm() -> str | None:
    """
    Return the user-preference text stored in `user_preferences.txt`,
    or ``None`` if the file does not exist.
    """
    filename = Path(USER_PREFERENCES_PATH) / "user_preferences.txt"

    try:
        with filename.open() as f:
            prefs = f.read()
        logger.info(f"User preferences loaded from: {filename}")
        return prefs
    except FileNotFoundError:
        logger.warning(f"User preference file not found: {filename}")
        return None


# --------------------------------------------------------------------------- #
#  Job-application context helpers
# --------------------------------------------------------------------------- #

_APPLY_CTX_FILE = Path(__file__).parent / "job_apply_context.json"


def set_job_apply_context(url: str, profile: Dict[str, Any]) -> None:
    """
    Persist the *single* job-apply session so any agent can fetch it later.

    Parameters
    ----------
    url : str
        Link to the job’s application page.
    profile : dict
        The user profile (already deserialized from JSON).
    """
    data = {"url": url, "profile": profile}
    _APPLY_CTX_FILE.write_text(json.dumps(data, indent=2))


def get_job_apply_context() -> Dict[str, Any] | None:
    """Return the saved apply-context dict, or ``None`` if none is stored."""
    if _APPLY_CTX_FILE.exists():
        return json.loads(_APPLY_CTX_FILE.read_text())
    return None
