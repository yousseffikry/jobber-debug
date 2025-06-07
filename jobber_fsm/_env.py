"""
Load environment variables for the whole Jobber package.
"""
from pathlib import Path
from dotenv import load_dotenv
import os

# Get the absolute path to the project root (2 levels up from this file)
current_file = Path(__file__).resolve()
project_root = current_file.parents[2]  # Goes up to 'jobber' directory
env_path = project_root / ".env"

# Debug print
print(f"[_env.py] Looking for .env at: {env_path}")
print(f"[_env.py] .env exists: {env_path.exists()}")

# Load with override=True to ensure variables are set
if env_path.exists():
    load_dotenv(env_path, override=True)
    print(f"[_env.py] Loaded .env from {env_path}")
else:
    # Try current working directory as fallback
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        load_dotenv(cwd_env, override=True)
        print(f"[_env.py] Loaded .env from {cwd_env}")
    else:
        print("[_env.py] WARNING: No .env file found!")

# Verify critical variables
if os.getenv("OPENAI_API_KEY"):
    print("[_env.py] ✓ OPENAI_API_KEY is set")
else:
    print("[_env.py] ✗ OPENAI_API_KEY is NOT set")