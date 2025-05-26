from pathlib import Path
from dotenv import load_dotenv

repo_root = Path(__file__).resolve().parents[2]
print("DEBUG – loading", repo_root / ".env")   # add this line temporarily
load_dotenv(repo_root / ".env", override=False)