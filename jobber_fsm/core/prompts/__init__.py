"""Prompt-registry for Jobber.

• Any *.txt or *.json file placed in this folder is automatically loaded.
• Built-in FALLBACK_PROMPTS guarantee that every key referenced in the
  agents exists even when you haven’t provided your own file yet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

PROMPT_DIR = Path(__file__).parent
LLM_PROMPTS: Dict[str, str] = {}


# --------------------------------------------------------------------------- #
# 1) load user-supplied prompt files                                          #
# --------------------------------------------------------------------------- #
def _load_prompt_files() -> None:
    """
    Every *.txt / *.json here becomes an entry in LLM_PROMPTS.

    • my_prompt.txt   →  {"MY_PROMPT": "<file-contents>"}
    • foo.json        →  {"FOO": "..."} or multiple keys if the json
    """
    for f in PROMPT_DIR.iterdir():
        if f.is_dir() or f.name.startswith("__"):
            continue

        key_base = f.stem.upper()

        if f.suffix == ".txt":
            LLM_PROMPTS[key_base] = f.read_text(encoding="utf-8").strip()

        elif f.suffix == ".json":
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, str):
                LLM_PROMPTS[key_base] = data.strip()
            elif isinstance(data, dict):
                for k, v in data.items():
                    LLM_PROMPTS[k.upper()] = str(v).strip()
            else:
                raise ValueError(f"Unsupported JSON contents in {f!s}")


# --------------------------------------------------------------------------- #
# 2) minimal defaults so the app boots even with zero prompt files            #
# --------------------------------------------------------------------------- #
FALLBACK_PROMPTS = {
    # ───────── Planner ─────────
    "PLANNER_AGENT_PROMPT": (
        "You are Jobber-Planner.\n"
        "Your goal is to turn the high-level task of submitting a job application "
        "into a numbered list of small browser actions the Executor agent can run. "
        "Return JSON: { terminate: <bool>, content: <string|list> }"
    ),
    # ───────── Executor ────────
    "BROWSER_AGENT_PROMPT": (
        "You are Jobber-Executor.  Follow the plan **exactly** by choosing one "
        "of the available tool functions for each step.  Never ask the user "
        "unless absolutely necessary."
    ),
    # tool descriptions
    "OPEN_URL_PROMPT": "Navigate the browser to the given absolute URL.",
    "ENTER_TEXT_AND_CLICK_PROMPT": (
        "Find a text input, type the provided text, then click the specified button."
    ),
    "GET_DOM_WITH_CONTENT_TYPE_PROMPT": "Return the full page DOM and MIME type.",
    "CLICK_PROMPT": "Click the element that matches the supplied selector.",
    "GET_URL_PROMPT": "Return the current page URL.",
    "BULK_ENTER_TEXT_PROMPT": (
        "Fill multiple fields: each item has {selector, text}."
    ),
    "ENTER_TEXT_PROMPT": "Type text into the element that matches the selector.",
    "PRESS_KEY_COMBINATION_PROMPT": "Send a key or key-combo (e.g. Ctrl+S) to the page.",
    "EXTRACT_TEXT_FROM_PDF_PROMPT": "Extract raw text from a PDF file.",
    "UPLOAD_FILE_PROMPT": "Upload a local file into the active <input type=file>.",
}

# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #
_load_prompt_files()

# merge defaults *without* overwriting any key the user already provided
for k, v in FALLBACK_PROMPTS.items():
    LLM_PROMPTS.setdefault(k.upper(), v.strip())
