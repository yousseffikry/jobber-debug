"""
Prompt templates for Jobber FSM agents.
"""

from pathlib import Path
import json

# load your prompt templates once
_PROMPTS_FILE = Path(__file__).with_suffix(".json")   # prompts.json next to __init__.py
if _PROMPTS_FILE.exists():
    _LLM_PROMPTS: dict = json.loads(_PROMPTS_FILE.read_text())
else:
    _LLM_PROMPTS = {}

# ---- public API -----------------------------------------------------------
LLM_PROMPTS = _LLM_PROMPTS          # <-- re-export so other modules can `import LLM_PROMPTS`

__all__ = ["LLM_PROMPTS"]
