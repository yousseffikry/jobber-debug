"""
Fix environment variable loading issue
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Force load .env from the root directory
root_dir = Path(__file__).parent
env_path = root_dir / ".env"

if env_path.exists():
    print(f"Loading .env from: {env_path}")
    load_dotenv(env_path, override=True)
    
    # Verify it worked
    if os.getenv("OPENAI_API_KEY"):
        print("✓ OPENAI_API_KEY loaded successfully")
    else:
        print("✗ OPENAI_API_KEY still not set")
else:
    print(f"✗ .env file not found at: {env_path}")