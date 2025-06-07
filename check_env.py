"""
Check environment and OpenAI setup
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env FIRST before any checks
root_dir = Path(__file__).parent
env_path = root_dir / ".env"
if env_path.exists():
    print(f"Pre-loading .env from: {env_path}")
    load_dotenv(env_path, override=True)

# Now continue with the rest of the script
print("=== Environment Check ===")

# Check .env file
env_files = ['.env', 'jobber/.env', 'jobber_fsm/.env']
for env_file in env_files:
    if Path(env_file).exists():
        print(f"✓ Found {env_file}")
        with open(env_file) as f:
            content = f.read()
            if 'OPENAI_API_KEY' in content:
                print(f"  - Contains OPENAI_API_KEY")
            if 'LANGCHAIN_API_KEY' in content:
                print(f"  - Contains LANGCHAIN_API_KEY")
    else:
        print(f"✗ {env_file} not found")

# Check environment variables
print("\n=== Environment Variables ===")
openai_key = os.getenv("OPENAI_API_KEY")
if openai_key:
    print(f"✓ OPENAI_API_KEY is set (length: {len(openai_key)})")
    print(f"  First 10 chars: {openai_key[:10]}...")
else:
    print("✗ OPENAI_API_KEY is NOT set")

langchain_key = os.getenv("LANGCHAIN_API_KEY")
if langchain_key:
    print(f"✓ LANGCHAIN_API_KEY is set (length: {len(langchain_key)})")
else:
    print("✗ LANGCHAIN_API_KEY is NOT set")

# Test OpenAI connection
print("\n=== Testing OpenAI Connection ===")
try:
    import openai
    client = openai.Client()
    
    # Try a simple completion
    response = client.chat.completions.create(
        model="gpt-4o-2024-08-06",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say 'test successful' if you can read this."}
        ],
        max_tokens=10
    )
    print(f"✓ OpenAI API call successful: {response.choices[0].message.content}")
except Exception as e:
    print(f"✗ OpenAI API call failed: {e}")
    import traceback
    traceback.print_exc()

# Check imports
print("\n=== Import Check ===")
# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from jobber_fsm.core.agent.planner_agent import PlannerAgent
    print("✓ Can import PlannerAgent")
except Exception as e:
    print(f"✗ Cannot import PlannerAgent: {e}")

try:
    from langsmith.wrappers import wrap_openai
    print("✓ Can import langsmith.wrappers")
except Exception as e:
    print(f"✗ Cannot import langsmith.wrappers: {e}")
    print("  Try: pip install langsmith")