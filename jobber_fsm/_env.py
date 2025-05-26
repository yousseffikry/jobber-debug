"""
Load environment variables for the whole Jobber package.

Executed **before** any module that might touch OpenAI or other
credential-hungry SDKs.
"""
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

# 1️⃣  Find nearest .env walking up from CWD; fall back to repo root
env_path = find_dotenv(usecwd=True) or Path(__file__).resolve().parents[2] / ".env"

# 2️⃣  Load it and **overwrite** empty or placeholder vars if they exist
load_dotenv(env_path, override=True)
