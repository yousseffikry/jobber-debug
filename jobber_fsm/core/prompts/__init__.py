from pathlib import Path
import json

PROMPT_DIR = Path(__file__).parent
LLM_PROMPTS = {}


def _load_prompts():
    """
    Any file inside core/prompts/ is loaded into LLM_PROMPTS.

    ─ *.txt  → the whole file is a prompt string  
    ─ *.json → expects {"KEY": "...prompt..."} or {"key1": "...", "key2": "..."}
               all keys are upper-cased in the final dict
    """
    for f in PROMPT_DIR.iterdir():
        if f.is_dir() or f.name.startswith("__"):
            continue

        stem = f.stem.upper()           # file basename, upper-cased
        if f.suffix == ".txt":
            LLM_PROMPTS[stem] = f.read_text(encoding="utf-8").strip()

        elif f.suffix == ".json":
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, str):
                LLM_PROMPTS[stem] = data.strip()
            elif isinstance(data, dict):
                for k, v in data.items():
                    LLM_PROMPTS[k.upper()] = v.strip()
            else:
                raise ValueError(f"Unsupported json in prompt file {f}")

        else:
            # ignore other file types
            pass


_load_prompts()
