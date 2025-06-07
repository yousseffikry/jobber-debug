# .gitignore

```
.env
.venv/
__pycache__
log_files/
logs/
.DS_STORE
results/
```

# .python-version

```
3.11.9

```

# .vscode/extensions.json

```json
{
  "recommendations": ["charliermarsh.ruff"]
}

```

# .vscode/settings.json

```json
{
  "[python]": {
    "editor.formatOnSave": true,
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.codeActionsOnSave": {
      "source.fixAll": "explicit",
      "source.organizeImports": "explicit"
    }
  },
  "notebook.formatOnSave.enabled": true,
  "notebook.codeActionsOnSave": {
    "notebook.source.fixAll": "explicit",
    "notebook.source.organizeImports": "explicit"
  },
  "ruff.nativeServer": "on"
}

```

# ai-apply-api/Dockerfile

```
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV PORT=8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 main:app
```

# ai-apply-api/main.py

```py
from flask import Flask, request, jsonify
from google.cloud import storage, run_v2
from google.api_core import exceptions
import json
import uuid
import os
import logging
from datetime import datetime

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
PROJECT_ID = os.environ.get('PROJECT_ID', 'jobber-ai-apply-prod')
LOCATION = os.environ.get('LOCATION', 'us-central1')
JOB_NAME = os.environ.get('JOB_NAME', 'jobber-apply')
BUCKET_NAME = os.environ.get('BUCKET_NAME', 'jobber-resumes-prod')

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'service': 'ai-apply-api'
    }), 200

@app.route('/apply', methods=['POST'])
def apply_to_job():
    """
    Endpoint called by mobile app to submit a job application
    
    Expected payload:
    {
        "userId": "string",
        "jobUrl": "string", 
        "resumeGcsPath": "gs://bucket/path/to/resume.pdf",
        "userProfile": {
            "email": "string",
            "first_name": "string", 
            "last_name": "string",
            "phone": "string",
            "visa_status": "string"
        }
    }
    """
    try:
        # Parse request
        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 400
            
        data = request.get_json()
        logger.info(f"Received apply request from user: {data.get('userId', 'unknown')}")
        
        # Validate required fields
        errors = []
        required_fields = ['userId', 'jobUrl', 'resumeGcsPath', 'userProfile']
        for field in required_fields:
            if field not in data or not data[field]:
                errors.append(f"Missing required field: {field}")
        
        # Validate user profile fields
        if 'userProfile' in data:
            profile = data['userProfile']
            profile_fields = ['email', 'first_name', 'last_name', 'phone', 'visa_status']
            for field in profile_fields:
                if field not in profile or not profile[field]:
                    errors.append(f"Missing user profile field: {field}")
            
            # Validate email format
            if 'email' in profile and '@' not in profile['email']:
                errors.append("Invalid email format")
        
        # Validate resume path format
        if 'resumeGcsPath' in data:
            if not data['resumeGcsPath'].startswith('gs://'):
                errors.append("resumeGcsPath must start with gs://")
        
        # Validate job URL format
        if 'jobUrl' in data:
            if not data['jobUrl'].startswith(('http://', 'https://')):
                errors.append("jobUrl must be a valid HTTP/HTTPS URL")
        
        if errors:
            return jsonify({
                'error': 'Validation failed',
                'errors': errors
            }), 400
        
        # Extract data
        user_id = data['userId']
        job_url = data['jobUrl']
        resume_gcs_path = data['resumeGcsPath']
        user_profile = data['userProfile']
        
        # Verify resume exists in GCS
        try:
            storage_client = storage.Client()
            bucket_name = resume_gcs_path.split('/')[2]
            blob_path = '/'.join(resume_gcs_path.split('/')[3:])
            bucket = storage_client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            
            if not blob.exists():
                return jsonify({
                    'error': 'Resume not found',
                    'message': f'Resume file not found at {resume_gcs_path}'
                }), 404
                
            logger.info(f"Verified resume exists: {resume_gcs_path}")
            
        except Exception as e:
            logger.error(f"Error checking resume: {e}")
            return jsonify({
                'error': 'Unable to verify resume',
                'message': str(e)
            }), 500
        
        # Create job ID
        job_id = f"{user_id}-{str(uuid.uuid4())[:8]}"
        
        # Prepare job data
        job_data = {
            'userId': user_id,
            'jobUrl': job_url,
            'resumeGcsPath': resume_gcs_path,
            'userProfile': user_profile
        }
        
        # Log the job submission
        logger.info(f"Submitting job {job_id} for user {user_id} to {job_url}")
        
        # Trigger Cloud Run Job
        try:
            client = run_v2.JobsClient()
            parent = f"projects/{PROJECT_ID}/locations/{LOCATION}/jobs/{JOB_NAME}"
            
            # Create the run request with environment overrides
            run_request = run_v2.RunJobRequest(
                name=parent,
                overrides=run_v2.RunJobRequest.Overrides(
                    container_overrides=[
                        run_v2.RunJobRequest.Overrides.ContainerOverride(
                            env=[
                                run_v2.EnvVar(name="JOB_DATA", value=json.dumps(job_data)),
                                run_v2.EnvVar(name="JOB_ID", value=job_id),
                            ]
                        )
                    ]
                )
            )
            
            # Execute the job
            operation = client.run_job(request=run_request)
            logger.info(f"Cloud Run Job triggered successfully: {job_id}")
            
            # Store initial status
            store_initial_status(job_id, user_id, job_url)
            
            return jsonify({
                'jobId': job_id,
                'status': 'processing',
                'message': 'Your job application has been queued for processing',
                'checkStatusUrl': f"/status/{job_id}"
            }), 202
            
        except exceptions.NotFound:
            logger.error(f"Cloud Run Job not found: {parent}")
            return jsonify({
                'error': 'Service configuration error',
                'message': 'Job processing service not found'
            }), 500
            
        except Exception as e:
            logger.error(f"Error triggering Cloud Run Job: {e}", exc_info=True)
            return jsonify({
                'error': 'Failed to submit job',
                'message': str(e)
            }), 500
        
    except Exception as e:
        logger.error(f"Unexpected error in apply endpoint: {e}", exc_info=True)
        return jsonify({
            'error': 'Internal server error',
            'message': 'An unexpected error occurred'
        }), 500

@app.route('/status/<job_id>', methods=['GET'])
def get_status(job_id):
    """Check application status by job ID"""
    try:
        # Sanitize job_id
        if not job_id or len(job_id) > 100:
            return jsonify({'error': 'Invalid job ID'}), 400
        
        # Read status from GCS
        client = storage.Client()
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(f'results/{job_id}.json')
        
        if blob.exists():
            content = blob.download_as_text()
            result = json.loads(content)
            
            # Add additional info
            result['retrievedAt'] = datetime.utcnow().isoformat()
            
            return jsonify(result), 200
        else:
            # Check if job was recently submitted
            initial_blob = bucket.blob(f'results/pending/{job_id}.json')
            if initial_blob.exists():
                return jsonify({
                    'jobId': job_id,
                    'status': 'processing',
                    'message': 'Application is being processed',
                    'retrievedAt': datetime.utcnow().isoformat()
                }), 200
            else:
                return jsonify({
                    'error': 'Job not found',
                    'jobId': job_id
                }), 404
        
    except Exception as e:
        logger.error(f"Error checking status for job {job_id}: {e}")
        return jsonify({
            'error': 'Error retrieving status',
            'message': str(e)
        }), 500

@app.route('/user/<user_id>/applications', methods=['GET'])
def get_user_applications(user_id):
    """Get all applications for a user"""
    try:
        client = storage.Client()
        bucket = client.bucket(BUCKET_NAME)
        
        # List all results for this user
        prefix = f'results/users/{user_id}/'
        blobs = bucket.list_blobs(prefix=prefix)
        
        applications = []
        for blob in blobs:
            try:
                content = blob.download_as_text()
                app_data = json.loads(content)
                applications.append(app_data)
            except Exception as e:
                logger.error(f"Error reading blob {blob.name}: {e}")
                continue
        
        # Sort by timestamp (newest first)
        applications.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        
        return jsonify({
            'userId': user_id,
            'applications': applications,
            'count': len(applications)
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting applications for user {user_id}: {e}")
        return jsonify({
            'error': 'Error retrieving applications',
            'message': str(e)
        }), 500

def store_initial_status(job_id: str, user_id: str, job_url: str):
    """Store initial pending status for a job"""
    try:
        client = storage.Client()
        bucket = client.bucket(BUCKET_NAME)
        
        initial_data = {
            'jobId': job_id,
            'userId': user_id,
            'jobUrl': job_url,
            'status': 'pending',
            'message': 'Job submitted and waiting to be processed',
            'submittedAt': datetime.utcnow().isoformat()
        }
        
        blob = bucket.blob(f'results/pending/{job_id}.json')
        blob.upload_from_string(json.dumps(initial_data, indent=2))
        
    except Exception as e:
        logger.error(f"Failed to store initial status: {e}")

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal server error: {e}")
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
```

# check_env.py

```py
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
```

# cloudbuild.yaml

```yaml
steps:
  # Build the container image
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', 'gcr.io/$PROJECT_ID/jobber-fsm:$BUILD_ID', '-f', 'Dockerfile.cloud', '.']
  
  # Push the container image
  - name: 'gcr.io/cloud-builders/docker'
    args: ['push', 'gcr.io/$PROJECT_ID/jobber-fsm:$BUILD_ID']
  
  # Tag as latest
  - name: 'gcr.io/cloud-builders/docker'
    args: ['tag', 'gcr.io/$PROJECT_ID/jobber-fsm:$BUILD_ID', 'gcr.io/$PROJECT_ID/jobber-fsm:latest']
  
  # Push latest tag
  - name: 'gcr.io/cloud-builders/docker'
    args: ['push', 'gcr.io/$PROJECT_ID/jobber-fsm:latest']
  
  # Update Cloud Run Job
  - name: 'gcr.io/google.com/cloudsdktool/cloud-sdk'
    entrypoint: gcloud
    args:
      - 'run'
      - 'jobs'
      - 'update'
      - 'jobber-apply'
      - '--image'
      - 'gcr.io/$PROJECT_ID/jobber-fsm:$BUILD_ID'
      - '--region'
      - 'us-central1'

images:
  - 'gcr.io/$PROJECT_ID/jobber-fsm:$BUILD_ID'
  - 'gcr.io/$PROJECT_ID/jobber-fsm:latest'
```

# Dockerfile

```
FROM python:3.11-slim

# Install system dependencies for Chrome/Chromium
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libgcc1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libstdc++6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    lsb-release \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Poetry
RUN pip install poetry

# Copy dependency files
COPY pyproject.toml poetry.lock ./

# Install Python dependencies (including playwright)
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi --no-root

# Set the browsers path environment variable BEFORE installing browsers
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install Playwright browsers - this is the critical part
RUN python -m playwright install chromium --with-deps

# Verify the installation
RUN ls -la /ms-playwright/chromium-*/chrome-linux/chrome || echo "Chrome binary not found!"

# Copy application code
COPY jobber_fsm ./jobber_fsm

# Set environment variables
ENV OPENAI_API_KEY=""
ENV LANGCHAIN_API_KEY=""

CMD ["python", "-u", "-m", "jobber_fsm.cloud_job_runner"]
```

# fix_env.py

```py
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
```

# jobber_fsm/__init__.py

```py
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

```

# jobber_fsm/__main__.py

```py
import asyncio

from jobber_fsm.core.agent.browser_nav_agent import BrowserNavAgent
from jobber_fsm.core.agent.planner_agent import PlannerAgent
from jobber_fsm.core.models.models import State
from jobber_fsm.core.orchestrator.orchestrator import Orchestrator


async def main():
    # Define state machine
    state_to_agent_map = {
        State.PLAN: PlannerAgent(),
        State.BROWSE: BrowserNavAgent(),
    }

    orchestrator = Orchestrator(state_to_agent_map=state_to_agent_map)
    await orchestrator.start()


if __name__ == "__main__":
    asyncio.run(main())

```

# jobber_fsm/_env.py

```py
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
```

# jobber_fsm/cloud_job_runner.py

```py
"""
Cloud runner for Jobber FSM - Production ready for mobile app integration
"""
import os
import sys

print(f"[cloud_job_runner] Starting script", flush=True)
print(f"[cloud_job_runner] Python path: {sys.path}", flush=True)
print(f"[cloud_job_runner] Current dir: {os.getcwd()}", flush=True)
print(f"[cloud_job_runner] Directory contents: {os.listdir('.')}", flush=True)

import json
import asyncio
from google.cloud import storage
import tempfile
import datetime
import time

from jobber_fsm.core.agent.planner_agent import PlannerAgent
from jobber_fsm.core.agent.browser_nav_agent import BrowserNavAgent
from jobber_fsm.core.models.models import State, PlannerInput
from jobber_fsm.core.orchestrator.orchestrator import Orchestrator
from jobber_fsm.core.memory import ltm
from jobber_fsm.utils.logger import logger
from jobber_fsm.utils.screenshot_debugger import ScreenshotDebugger

print("[cloud_job_runner] Script starting...", flush=True)

def parse_visa_status(visa_status):
    """
    Convert visa status to expected format.
    Handles both string and object formats.
    
    Examples:
    - String: "US Citizen"
    - Object: {"United States": "Visa not required", "Canada": "Work permit required"}
    """
    if isinstance(visa_status, str):
        # Original string parsing
        visa_lower = visa_status.lower()
        
        if any(term in visa_lower for term in ["citizen", "green card", "permanent resident", "visa not required"]):
            return [{"code": "US", "name": "United States", "requiresSponsorship": False}]
        elif any(visa in visa_status.upper() for visa in ["H1B", "H-1B", "F1", "F-1", "J1", "J-1"]):
            return [{"code": "US", "name": "United States", "requiresSponsorship": True}]
        else:
            return []
    
    elif isinstance(visa_status, dict):
        # Handle object format: {"United States": "Visa not required", "Canada": "Work permit required"}
        visa_countries = []
        
        country_codes = {
            "United States": "US",
            "Canada": "CA",
            "United Kingdom": "GB",
            "Australia": "AU",
            "Germany": "DE",
        }
        
        for country, status in visa_status.items():
            code = country_codes.get(country, country[:2].upper())
            status_lower = status.lower()
            requires_sponsorship = not any(term in status_lower for term in [
                "citizen", "not required", "permanent resident", "green card", "authorized"
            ])
            
            visa_countries.append({
                "code": code,
                "name": country,
                "requiresSponsorship": requires_sponsorship,
                "status": status
            })
        
        return visa_countries
    
    return []


def parse_disability_status(disability_status):
    """Parse disability status array"""
    if isinstance(disability_status, str):
        return disability_status
    elif isinstance(disability_status, list):
        if len(disability_status) == 0:
            return "No"
        else:
            return f"Yes - {', '.join(disability_status)}"
    return "Prefer not to say"


# In your cloud_job_runner.py, update the profile building section:
def build_complete_profile(user_profile, local_resume_path):
    """Build complete profile with support for complex data types"""
    
    # Parse visa status (handles string or object)
    visa_countries = parse_visa_status(user_profile.get('visa_status', ''))
    
    # Parse disability status (handles string or array)
    disability_status = parse_disability_status(user_profile.get('disability_status', ''))
    
    complete_profile = {
        'first_name': user_profile.get('first_name', ''),
        'last_name': user_profile.get('last_name', ''),
        'email': user_profile.get('email', ''),
        'phone': user_profile.get('phone', ''),
        'linkedin_profile': user_profile.get('linkedin_profile', ''),
        'date_of_birth': user_profile.get('date_of_birth', ''),
        'gender': user_profile.get('gender', 'Prefer not to say'),
        'race_ethnicity': user_profile.get('race_ethnicity', 'Prefer not to say'),
        'veteran_status': user_profile.get('veteran_status', 'Prefer not to say'),
        'disability_status': disability_status,
        'visa_countries': visa_countries,
        'resume_path': local_resume_path
    }
    
    return complete_profile

async def main():
    print("[cloud_job_runner] main() function started", flush=True)
    start_time = time.time()
    
    print("[cloud_job_runner] Getting environment variables", flush=True)
    logger.info(f"[cloud_job_runner] Starting main()")
    logger.info(f"[cloud_job_runner] Python version: {sys.version}")
    logger.info(f"[cloud_job_runner] Current directory: {os.getcwd()}")
    logger.info(f"[cloud_job_runner] Environment variables:")
    for key in ['OPENAI_API_KEY', 'JOB_DATA', 'JOB_ID', 'K_SERVICE', 'K_REVISION', 'CLOUD_RUN_JOB', 'GOOGLE_CLOUD_PROJECT']:
        value = os.environ.get(key, 'NOT SET')
        if key == 'OPENAI_API_KEY' and value != 'NOT SET':
            value = value[:10] + '...'  # Mask API key
        logger.info(f"  {key}: {value}")
    
    # Get job details from environment
    job_data_str = os.environ.get('JOB_DATA', '{}')
    job_id = os.environ.get('JOB_ID', str(int(time.time())))
    
    logger.info(f"Starting job {job_id}")
    logger.info(f"Raw JOB_DATA: {job_data_str}")
    
    try:
        job_data = json.loads(job_data_str)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JOB_DATA: {e}")
        await store_result('unknown', 'unknown', job_id, "failed", f"Invalid JOB_DATA format: {e}")
        return
    
    # Extract required fields
    user_id = job_data.get('userId')
    job_url = job_data.get('jobUrl')
    resume_gcs_path = job_data.get('resumeGcsPath')
    user_profile = job_data.get('userProfile', {})
    
    if not all([user_id, job_url, resume_gcs_path]):
        error_msg = f"Missing required fields. userId: {user_id}, jobUrl: {job_url}, resumeGcsPath: {resume_gcs_path}"
        logger.error(error_msg)
        await store_result(user_id or 'unknown', job_url or 'unknown', job_id, "failed", error_msg)
        return
    
    logger.info(f"Processing job application:")
    logger.info(f"  User ID: {user_id}")
    logger.info(f"  Job URL: {job_url}")
    logger.info(f"  Resume: {resume_gcs_path}")
    logger.info(f"  Profile: {json.dumps(user_profile, indent=2)}")
    
    try:
        # Download resume from GCS
        logger.info("Downloading resume from GCS...")
        local_resume_path = await download_resume(resume_gcs_path)
        logger.info(f"Resume downloaded to: {local_resume_path}")
        
        # Build complete profile for the AI agent
        logger.info("Building complete profile...")
        complete_profile = build_complete_profile(user_profile, local_resume_path)
        
        logger.info(f"Complete profile prepared: {json.dumps(complete_profile, indent=2)}")
        
        # Set up job context for the AI agent
        logger.info("Setting up job context in LTM...")
        ltm.set_job_apply_context(url=job_url, profile=complete_profile)
        
        # Initialize screenshot debugger
        logger.info("Initializing screenshot debugger...")
        screenshot_debugger = ScreenshotDebugger(job_id)

        # Test screenshot capability
        logger.info("Testing screenshot capability...")
        test_result = await screenshot_debugger.test_screenshot()
        logger.info(f"Screenshot test result: {test_result}")
        
        # Create agents
        logger.info("Creating AI agents...")
        planner = PlannerAgent(auto_mode=True)
        logger.info("  PlannerAgent created")
        browser_agent = BrowserNavAgent(planner, auto_mode=True)
        logger.info("  BrowserNavAgent created")
        
        # Inject screenshot debugger into browser agent
        browser_agent.screenshot_debugger = screenshot_debugger
        
        state_map = {
            State.PLAN: planner,
            State.BROWSE: browser_agent,
        }
        
        # Initialize orchestrator
        logger.info("Initializing orchestrator...")
        orch = Orchestrator(state_to_agent_map=state_map)
        await orch._bootstrap()
        logger.info("Orchestrator bootstrap completed")

        # Test screenshot capability
        logger.info("Testing screenshot capability...")
        try:
            from jobber_fsm.core.web_driver.playwright import PlaywrightManager
            browser_manager = PlaywrightManager()
            page = await browser_manager.get_current_page()
            if page:
                test_path = await screenshot_debugger.capture(
                    page, 
                    "test_initial", 
                    f"Initial page state - URL: {page.url}"
                )
                logger.info(f"Test screenshot result: {test_path}")
            else:
                logger.warning("No page available for test screenshot")
        except Exception as e:
            logger.error(f"Screenshot test failed: {e}", exc_info=True)
        
        # Create the planning input
        planner_input = PlannerInput(
            objective=f"Apply to this job: {job_url}. Use the resume at {local_resume_path} and the provided profile information.",
            plan=None,
            completed_tasks=None,
            task_for_review=None,
        )
        
        # Run the application process
        logger.info("Starting job application process...")
        logger.info(f"  Objective: {planner_input.objective}")
        result = await planner.process_query(planner_input)
        logger.info("Job application process completed")
        
        # Calculate execution time
        execution_time = time.time() - start_time
        
        success_message = f"Application completed successfully in {execution_time:.2f} seconds"
        if hasattr(result, 'final_response') and result.final_response:
            success_message = f"{success_message}. Details: {result.final_response}"
        
        logger.info(success_message)
        
        # Store success result with screenshot summary
        logger.info("Creating screenshot summary...")
        summary_url = await screenshot_debugger.create_summary()
        success_message_with_debug = f"{success_message}\nDebug screenshots: {summary_url}"
        
        logger.info("Storing success result...")
        await store_result(user_id, job_url, job_id, "completed", success_message_with_debug)
        
        # Clean up
        if os.path.exists(local_resume_path):
            os.remove(local_resume_path)
            logger.info("Cleaned up temporary resume file")
        
    except Exception as e:
        error_msg = f"Application failed: {str(e)}"
        logger.error(f"[cloud_job_runner] {error_msg}", exc_info=True)
        await store_result(user_id, job_url, job_id, "failed", error_msg)
        raise

async def download_resume(gcs_path: str) -> str:
    """Download resume from GCS to temp file"""
    try:
        client = storage.Client()
        
        # Parse gs://bucket/path
        if not gcs_path.startswith('gs://'):
            raise ValueError(f"Invalid GCS path format: {gcs_path}")
        
        parts = gcs_path.replace('gs://', '').split('/', 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid GCS path format: {gcs_path}")
        
        bucket_name, blob_path = parts
        
        logger.info(f"Downloading from bucket: {bucket_name}, path: {blob_path}")
        
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        
        # Check if blob exists
        if not blob.exists():
            raise FileNotFoundError(f"Resume not found at {gcs_path}")
        
        # Determine file extension
        suffix = '.pdf' if blob_path.lower().endswith('.pdf') else '.txt'
        
        # Download to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            blob.download_to_file(tmp_file)
            logger.info(f"Downloaded {blob.size} bytes to {tmp_file.name}")
            return tmp_file.name
            
    except Exception as e:
        logger.error(f"Failed to download resume: {e}")
        raise

async def store_result(user_id: str, job_url: str, job_id: str, status: str, message: str):
    """Store result in GCS for retrieval"""
    try:
        client = storage.Client()
        bucket = client.bucket('jobber-resumes-prod')
        
        result_data = {
            'jobId': job_id,
            'userId': user_id,
            'jobUrl': job_url,
            'status': status,
            'message': message,
            'timestamp': datetime.datetime.utcnow().isoformat(),
            'executionTime': os.environ.get('EXECUTION_TIME', 'unknown')
        }
        
        # Store by job ID for easy lookup
        blob = bucket.blob(f'results/{job_id}.json')
        blob.upload_from_string(json.dumps(result_data, indent=2))
        
        # Also store by user ID for user history
        user_blob = bucket.blob(f'results/users/{user_id}/{job_id}.json')
        user_blob.upload_from_string(json.dumps(result_data, indent=2))
        
        logger.info(f"Stored result: {status} for job {job_id}")
        
    except Exception as e:
        logger.error(f"Failed to store result: {e}")
        # Don't raise here - we still want the job to complete even if result storage fails

print("[cloud_job_runner] About to check __main__", flush=True)

if __name__ == "__main__":
    print("[cloud_job_runner] In __main__ block", flush=True)
    # Set up asyncio for Cloud Run
    try:
        print("[cloud_job_runner] About to run main()", flush=True)
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Job interrupted")
    except Exception as e:
        logger.error(f"Job failed with error: {e}")
        print(f"[cloud_job_runner] Fatal error: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
```

# jobber_fsm/config/__init__.py

```py

```

# jobber_fsm/config/config.py

```py
# config.py at the project source code root
import os

PROJECT_SOURCE_ROOT = os.path.dirname(os.path.abspath(__file__))
SOURCE_LOG_FOLDER_PATH = os.path.join(PROJECT_SOURCE_ROOT, 'log_files')

PROJECT_ROOT = os.path.dirname(PROJECT_SOURCE_ROOT)

PROJECT_TEMP_PATH = os.path.join(PROJECT_ROOT, 'temp')

USER_PREFERENCES_PATH = os.path.join(PROJECT_SOURCE_ROOT, 'user_preferences')
PROJECT_TEST_ROOT = os.path.join(PROJECT_ROOT, 'test')

# Check if the log folder exists, and if not, create it
if not os.path.exists(SOURCE_LOG_FOLDER_PATH):
    os.makedirs(SOURCE_LOG_FOLDER_PATH)
    print(f"Created log folder at: {SOURCE_LOG_FOLDER_PATH}")

#create user prefernces folder if it does not exist
if not os.path.exists(USER_PREFERENCES_PATH):
    os.makedirs(USER_PREFERENCES_PATH)
    print(f"Created user preferences folder at: {USER_PREFERENCES_PATH}")

if not os.path.exists(PROJECT_TEMP_PATH):
    os.makedirs(PROJECT_TEMP_PATH)
    print(f"Created temp folder at: {PROJECT_TEMP_PATH}")

```

# jobber_fsm/config/log_files/json_accessibility_dom_enriched.json

```json
{
  "role": "WebArea",
  "name": "Senior Project Manager and Technical Consultant | Toronto, ON, Canada | Arcadis",
  "children": [
    {
      "name": "\uf177 All Jobs",
      "mmid": "208",
      "tag": "button"
    },
    {
      "role": "button",
      "name": "Share position: Senior Project Manager and Technical Consultant",
      "mmid": "214",
      "tag": "div"
    },
    {
      "name": "Apply Now",
      "mmid": "233",
      "tag": "button"
    },
    {
      "name": "Upload Your Resume",
      "mmid": "308",
      "tag": "button"
    },
    {
      "role": "button",
      "name": " Slide 1",
      "mmid": "442",
      "tag": "a"
    },
    {
      "role": "button",
      "name": " Slide 2",
      "mmid": "444",
      "tag": "a"
    },
    {
      "role": "button",
      "name": " Slide 3",
      "mmid": "446",
      "tag": "a"
    },
    {
      "role": "button",
      "name": " Slide 1",
      "mmid": "490",
      "tag": "a"
    },
    {
      "role": "button",
      "name": " Slide 2",
      "mmid": "492",
      "tag": "a"
    },
    {
      "role": "button",
      "name": " Slide 3",
      "mmid": "494",
      "tag": "a"
    },
    {
      "role": "button",
      "name": " Slide 4",
      "mmid": "496",
      "tag": "a"
    },
    {
      "role": "button",
      "name": " Slide 5",
      "mmid": "498",
      "tag": "a"
    }
  ]
}
```

# jobber_fsm/config/log_files/json_accessibility_dom.json

```json
{
  "role": "WebArea",
  "name": "Senior Project Manager and Technical Consultant | Toronto, ON, Canada | Arcadis",
  "children": [
    {
      "role": "link",
      "name": "Skip to main content",
      "keyshortcuts": "196"
    },
    {
      "role": "link",
      "name": "Company logo",
      "keyshortcuts": "197"
    },
    {
      "role": "link",
      "name": " Join Talent Network",
      "keyshortcuts": "199"
    },
    {
      "role": "link",
      "name": "\uf177 All Jobs",
      "keyshortcuts": "208"
    },
    {
      "role": "button",
      "name": "Share position: Senior Project Manager and Technical Consultant",
      "keyshortcuts": "214",
      "haspopup": "menu"
    },
    {
      "role": "heading",
      "name": "Senior Project Manager and Technical Consultant",
      "keyshortcuts": "220",
      "level": 2
    },
    {
      "role": "text",
      "name": "Toronto, ON, Canada"
    },
    {
      "role": "text",
      "name": "Hybrid"
    },
    {
      "role": "button",
      "name": "Apply Now",
      "keyshortcuts": "233"
    },
    {
      "role": "heading",
      "name": "Job Description",
      "keyshortcuts": "244",
      "level": 3
    },
    {
      "role": "heading",
      "name": "Job ID",
      "keyshortcuts": "248",
      "level": 4
    },
    {
      "role": "text",
      "name": "31541"
    },
    {
      "role": "heading",
      "name": "Date Posted",
      "keyshortcuts": "251",
      "level": 4
    },
    {
      "role": "text",
      "name": "2025-05-27"
    },
    {
      "role": "text",
      "name": "\u00a0"
    },
    {
      "role": "text",
      "name": "Arcadis is the world's leading company delivering sustainable design, engineering, and consultancy solutions for natural and built assets."
    },
    {
      "role": "text",
      "name": "We are more than 36,000 people, in over 70 countries, dedicated to\u00a0improving quality of life. Everyone has an important role to play. With the power of many curious minds, together we can solve the world\u2019s most complex challenges and deliver more impact together."
    },
    {
      "role": "text",
      "name": "Role description:"
    },
    {
      "role": "text",
      "name": "Arcadis Canada is seeking a highly experienced and strategic Senior Project Manager and Technical Consultant to join the Intelligent Mobility Services team to lead digital asset management and digital transformation initiatives with our clients. This role will involve managing complex projects, providing expert guidance on digital strategies, and driving organizational change to deliver measurable outcomes. The ideal candidate will have a deep understanding of digital technologies, business transformation, and project management methodologies, coupled with excellent leadership and communication skills."
    },
    {
      "role": "text",
      "name": "This role will sit within the larger Global Mobility Business Area. We partner with our clients across the globe to design thriving and connected cities and communities that enable opportunity for all and keep the world moving. Climate change, urbanization and digitization trends are requiring today\u2019s mobility projects and systems to address an evolving set of demands from the world\u2019s growing population. We design connected, sustainable solutions that integrate existing infrastructure with new technologies, and optimize the mobility of people and goods."
    },
    {
      "role": "text",
      "name": "Role accountabilities:"
    },
    {
      "role": "text",
      "name": "Project Management:"
    },
    {
      "role": "text",
      "name": "As Senior Project Manager you will lead and manage the end-to-end delivery of projects, ensuring scope, budget and timelines are met.\u00a0 This includes preparing project plans, which includes deliverables, milestones, resource allocation and risk management strategies. As Project Manager, you will monitor project performance and progress using KPIs, and provide regular updates to stakeholders through reports and presentations."
    },
    {
      "role": "text",
      "name": "Client Relationship Management:"
    },
    {
      "role": "text",
      "name": "The Project Manager is the primary point of contact for clients during project lifecycles, addressing concerns and providing regular updates. Managing the client relationships is a key aspect of the work.\u00a0 This includes managing internal stakeholders and external clients, ensuring their needs are met consistently and proactively.\u00a0 In many projects, workshops are used to facilitate client engagement.\u00a0You will manage these workshops along with client meetings, and presentations to gather client feedback and to align with project expectations."
    },
    {
      "role": "text",
      "name": "Your objective is to build long-term relationships based on trust, reliability, and delivering value."
    },
    {
      "role": "text",
      "name": "Occasionally, the development and support of technical content for proposal deliveries are required.\u00a0"
    },
    {
      "role": "text",
      "name": "Technical and Solution Delivery:"
    },
    {
      "role": "text",
      "name": "In this role, you will take ownership of translating business needs into functional and technical specifications, guiding solution delivery teams through the configuration, testing, and deployment of systems. You will spearhead the integration of new systems with existing platforms, overseeing data migration, system upgrades, and implementation processes, all while addressing challenges and optimizing solutions."
    },
    {
      "role": "text",
      "name": "As a key driver of digital transformation, you will provide strategic leadership on emerging trends, technologies, and best practices to future-proof organizational or client operations. You will identify, recommend, and implement technology solutions, platforms, and tools to enhance operational efficiency, elevate customer experiences, and enable data-driven decision-making."
    },
    {
      "role": "text",
      "name": "Leadership and Mentorship:"
    },
    {
      "role": "text",
      "name": "You will provide guidance and mentorship to junior and intermediate analysts and team members, share your expertise and insights to foster a collaborative and growth-oriented environment."
    },
    {
      "role": "text",
      "name": "Qualifications & Experience:"
    },
    {
      "role": "text",
      "name": "Bachelor\u2019s degree in Engineering, Computer Science, Information Technology, or related field (master's degree is preferred).\u00a0"
    },
    {
      "role": "text",
      "name": "15+ years of experience in business and systems analysis, delivering end-to-end solutions in complex environments.\u00a0"
    },
    {
      "role": "text",
      "name": "PMP, PRINCE2, Agile, or Scrum certifications are highly desirable"
    },
    {
      "role": "text",
      "name": "Strong understanding of digital technologies such as Cloud, AI, IoT, RPA, Data Analytics, GIS, and CRM/ERP systems"
    },
    {
      "role": "text",
      "name": "Familiarity with the public sector client environment will be an asset."
    },
    {
      "role": "text",
      "name": "Why Arcadis?"
    },
    {
      "role": "text",
      "name": "We can only achieve our goals when everyone is empowered to be their best. We believe everyone's contribution matters. It\u2019s why we are pioneering a skills-based approach, where you can harness your unique experience and expertise to carve your career path and maximize the impact we can make together."
    },
    {
      "role": "text",
      "name": "You\u2019ll do meaningful work, and no matter what role, you\u2019ll be helping to deliver sustainable solutions for a more prosperous planet. Make your mark, on your career, your colleagues, your clients, your life and the world around you."
    },
    {
      "role": "text",
      "name": "Together, we can create a lasting legacy."
    },
    {
      "role": "text",
      "name": "Join Arcadis. Create a Legacy."
    },
    {
      "role": "text",
      "name": "Our Commitment to Equality, Diversity, Inclusion & Belonging"
    },
    {
      "role": "text",
      "name": "We want you to be able to bring your best self to work every day which is why we take equality and inclusion seriously and hold ourselves to account for our actions. Our ambition is to be an employer of choice and provide a great place to work for all our people. We are an equal opportunity and affirmative action employer. Women, minorities, people with disabilities and veterans are strongly encouraged to apply. We are dedicated to a policy of non-discrimination in employment on any basis including race, creed, color, religion, national origin, sex, age, disability, marital status, sexual orientation, gender identity, citizenship status, disability, veteran status, or any other basis prohibited by law."
    },
    {
      "role": "text",
      "name": "The salary range for this position is $114,590-$212,810. The base salary represents Arcadis\u2019 hiring range for this position. Actual salaries will vary and will be based on various factors, such as location, skills, experience, and qualification for the role."
    },
    {
      "role": "heading",
      "name": "Get Matched",
      "keyshortcuts": "306",
      "level": 3
    },
    {
      "role": "text",
      "name": "Upload Your Resume And See Jobs That Match Your Skills And Experience"
    },
    {
      "role": "button",
      "name": "Upload Your Resume",
      "keyshortcuts": "308"
    },
    {
      "role": "text",
      "name": "Match Unknown"
    },
    {
      "role": "heading",
      "name": "Other Similar Jobs",
      "keyshortcuts": "316",
      "level": 2
    },
    {
      "role": "link",
      "name": "Senior Project Manager \uf3c5 Phoenix, AZ, United States Program Management",
      "keyshortcuts": "324"
    },
    {
      "role": "link",
      "name": "Senior Project Manager - Workplace \uf3c5 Houston, TX, United States and 1 more Architecture Hybrid",
      "keyshortcuts": "333"
    },
    {
      "role": "link",
      "name": "Senior Project Manager (Rail & Transit) \uf3c5 Toronto, ON, Canada Transportation Engineering Hybrid",
      "keyshortcuts": "346"
    },
    {
      "role": "button",
      "name": " Slide 1",
      "keyshortcuts": "442"
    },
    {
      "role": "button",
      "name": " Slide 2",
      "keyshortcuts": "444"
    },
    {
      "role": "button",
      "name": " Slide 3",
      "keyshortcuts": "446"
    },
    {
      "role": "text",
      "name": "Slide 1 of 3"
    },
    {
      "role": "heading",
      "name": "Recommended Videos For You",
      "keyshortcuts": "452",
      "level": 2
    },
    {
      "role": "Iframe",
      "name": "A Day in the Life of Our CEO, Alan Brookes",
      "keyshortcuts": "461"
    },
    {
      "role": "heading",
      "name": "A Day In The Life Of Our CEO, Alan Brookes",
      "keyshortcuts": "462",
      "level": 3
    },
    {
      "role": "text",
      "name": "Get to know our new CEO, Alan Brookes, as he shares some of his aspirations and vision for the future."
    },
    {
      "role": "button",
      "name": " Slide 1",
      "keyshortcuts": "490"
    },
    {
      "role": "button",
      "name": " Slide 2",
      "keyshortcuts": "492"
    },
    {
      "role": "button",
      "name": " Slide 3",
      "keyshortcuts": "494"
    },
    {
      "role": "button",
      "name": " Slide 4",
      "keyshortcuts": "496"
    },
    {
      "role": "button",
      "name": " Slide 5",
      "keyshortcuts": "498"
    },
    {
      "role": "text",
      "name": "Slide 1 of 5"
    },
    {
      "role": "heading",
      "name": "Perks And Benefits",
      "keyshortcuts": "504",
      "level": 2
    },
    {
      "role": "heading",
      "name": "Global Employee Assistance Program (EAP)",
      "keyshortcuts": "510",
      "level": 3
    },
    {
      "role": "text",
      "name": "Your well-being matters to us, and we are committed to providing the support you need, whenever you need it. The EAP provides confidential counselling and support services for various personal and work-related situations or concerns."
    },
    {
      "role": "heading",
      "name": "Flexible Working",
      "keyshortcuts": "515",
      "level": 3
    },
    {
      "role": "text",
      "name": "Our culture fosters individual growth within a supportive community, where flexibility isn't just a perk \u2013 it's our promise. With a longstanding commitment to work-life balance, we empower you to tailor your schedule to fit your priorities. Whether it's balancing family commitments, pursuing personal passions, or optimizing productivity, our flexible working is here to support your journey."
    },
    {
      "role": "heading",
      "name": "Inclusive Culture",
      "keyshortcuts": "520",
      "level": 3
    },
    {
      "role": "text",
      "name": "In our inclusive culture, diversity thrives, fostering an environment where everyone's unique perspectives are valued, supported and respected, empowering individuals to be their best and full selves, every day and collectively solve the world's most complex challenges."
    },
    {
      "role": "heading",
      "name": "Learning & Development",
      "keyshortcuts": "525",
      "level": 3
    },
    {
      "role": "text",
      "name": "At Arcadis, our commitment to continuous learning and development is exemplified through our Skills Powered approach, which integrates the innovative programs and resources offered in our Basecamp (Expedition DNA). This holistic strategy cultivates talent, propelling career growth and ensuring our team remains at the forefront of an ever-evolving industry landscape."
    },
    {
      "role": "heading",
      "name": "Reminder: No Unsolicited Submissions. Direct Applications Only",
      "keyshortcuts": "531",
      "level": 1
    },
    {
      "role": "text",
      "name": "Please note Arcadis does not accept unsolicited CVs from recruitment agencies and no fees will be paid in relation to such submissions. All candidates must apply directly through our official channels. We appreciate your cooperation and understanding."
    },
    {
      "role": "link",
      "name": "Powered by\u00a0 Powered by Eightfold.ai \u00a0 eightfold.ai #WhatsNextForYou",
      "description": "Visit Eightfold.ai homepage",
      "keyshortcuts": "535"
    },
    {
      "role": "Iframe",
      "name": "chatbot",
      "keyshortcuts": "537"
    },
    {
      "role": "IframePresentational",
      "name": "reCAPTCHA",
      "keyshortcuts": "567"
    }
  ]
}
```

# jobber_fsm/config/log_files/text_only_dom.txt

```txt
Skip to main content
 Join Talent Network
 All Jobs

Senior Project Manager and Technical Consultant

Toronto, ON, Canada

Hybrid

Apply Now
Job Description
Job ID
31541
Date Posted
2025-05-27

 

Arcadis is the world's leading company delivering sustainable design, engineering, and consultancy solutions for natural and built assets.

We are more than 36,000 people, in over 70 countries, dedicated to improving quality of life. Everyone has an important role to play. With the power of many curious minds, together we can solve the world’s most complex challenges and deliver more impact together.

Role description:

Arcadis Canada is seeking a highly experienced and strategic Senior Project Manager and Technical Consultant to join the Intelligent Mobility Services team to lead digital asset management and digital transformation initiatives with our clients. This role will involve managing complex projects, providing expert guidance on digital strategies, and driving organizational change to deliver measurable outcomes. The ideal candidate will have a deep understanding of digital technologies, business transformation, and project management methodologies, coupled with excellent leadership and communication skills.

This role will sit within the larger Global Mobility Business Area. We partner with our clients across the globe to design thriving and connected cities and communities that enable opportunity for all and keep the world moving. Climate change, urbanization and digitization trends are requiring today’s mobility projects and systems to address an evolving set of demands from the world’s growing population. We design connected, sustainable solutions that integrate existing infrastructure with new technologies, and optimize the mobility of people and goods.

Role accountabilities:

Project Management:

As Senior Project Manager you will lead and manage the end-to-end delivery of projects, ensuring scope, budget and timelines are met.  This includes preparing project plans, which includes deliverables, milestones, resource allocation and risk management strategies. As Project Manager, you will monitor project performance and progress using KPIs, and provide regular updates to stakeholders through reports and presentations.

Client Relationship Management:

The Project Manager is the primary point of contact for clients during project lifecycles, addressing concerns and providing regular updates. Managing the client relationships is a key aspect of the work.  This includes managing internal stakeholders and external clients, ensuring their needs are met consistently and proactively.  In many projects, workshops are used to facilitate client engagement. You will manage these workshops along with client meetings, and presentations to gather client feedback and to align with project expectations.

Your objective is to build long-term relationships based on trust, reliability, and delivering value.

Occasionally, the development and support of technical content for proposal deliveries are required. 

Technical and Solution Delivery:

In this role, you will take ownership of translating business needs into functional and technical specifications, guiding solution delivery teams through the configuration, testing, and deployment of systems. You will spearhead the integration of new systems with existing platforms, overseeing data migration, system upgrades, and implementation processes, all while addressing challenges and optimizing solutions.

As a key driver of digital transformation, you will provide strategic leadership on emerging trends, technologies, and best practices to future-proof organizational or client operations. You will identify, recommend, and implement technology solutions, platforms, and tools to enhance operational efficiency, elevate customer experiences, and enable data-driven decision-making.

Leadership and Mentorship:

You will provide guidance and mentorship to junior and intermediate analysts and team members, share your expertise and insights to foster a collaborative and growth-oriented environment.

Qualifications & Experience:

Bachelor’s degree in Engineering, Computer Science, Information Technology, or related field (master's degree is preferred). 
15+ years of experience in business and systems analysis, delivering end-to-end solutions in complex environments. 
PMP, PRINCE2, Agile, or Scrum certifications are highly desirable
Strong understanding of digital technologies such as Cloud, AI, IoT, RPA, Data Analytics, GIS, and CRM/ERP systems
Familiarity with the public sector client environment will be an asset.

Why Arcadis?

We can only achieve our goals when everyone is empowered to be their best. We believe everyone's contribution matters. It’s why we are pioneering a skills-based approach, where you can harness your unique experience and expertise to carve your career path and maximize the impact we can make together.

You’ll do meaningful work, and no matter what role, you’ll be helping to deliver sustainable solutions for a more prosperous planet. Make your mark, on your career, your colleagues, your clients, your life and the world around you.

Together, we can create a lasting legacy.

Join Arcadis. Create a Legacy.

Our Commitment to Equality, Diversity, Inclusion & Belonging

We want you to be able to bring your best self to work every day which is why we take equality and inclusion seriously and hold ourselves to account for our actions. Our ambition is to be an employer of choice and provide a great place to work for all our people. We are an equal opportunity and affirmative action employer. Women, minorities, people with disabilities and veterans are strongly encouraged to apply. We are dedicated to a policy of non-discrimination in employment on any basis including race, creed, color, religion, national origin, sex, age, disability, marital status, sexual orientation, gender identity, citizenship status, disability, veteran status, or any other basis prohibited by law.

The salary range for this position is $114,590-$212,810. The base salary represents Arcadis’ hiring range for this position. Actual salaries will vary and will be based on various factors, such as location, skills, experience, and qualification for the role.
Get Matched
Upload Your Resume And See Jobs That Match Your Skills And Experience
Upload Your Resume
Match Unknown
Other Similar Jobs
Senior Project Manager

Phoenix, AZ, United States

Program Management
Senior Project Manager - Workplace

Houston, TX, United States and 1 more

Architecture
Hybrid
Senior Project Manager (Rail & Transit)

Toronto, ON, Canada

Transportation Engineering
Hybrid
Senior Federal and Climate Adaptation Project Manager

San Francisco, CA, United States

Program Management
Hybrid
Senior Civil Engineering Project Manager - Houston

Houston, TX, United States and 23 more

Program Management
Hybrid
Senior Project & Program Consultant

Iasi, Moldavia, Romania and 1 more

Project Management
Hybrid
Senior Federal and Climate Adaptation Project Manager

New York, NY, United States and 1 more

Program Management
Hybrid
Project Manager - Parks and Tidal

Los Angeles, CA, United States

Project Management
Senior Transportation Project Manager

Baton Rouge, LA, United States and 1 more

Project Management
Hybrid
Slide 1 of 3
Recommended Videos For You
A Day In The Life Of Our CEO, Alan Brookes

Get to know our new CEO, Alan Brookes, as he shares some of his aspirations and vision for the future.

I Belong: Ian Story

Belonging at Arcadis means equitable access for all - including our colleagues with physical disabilities and/or neurodivergent conditions. At Arcadis...

I Belong: Donnell's Story

Over February we've been celebrating Black History Month with our colleagues in the United States. This year's theme was the Past, Present and Future ...

Arcadis In One Word

What Arcadis means to Arcadians.

I Belong: Being, Believing, Belonging

I Belong is a campaign for everyone. It's a feeling, it's a celebration, it’s our desire to create a truly welcoming workplace for all. Arcadis exist...

Slide 1 of 5
Perks And Benefits
Global Employee Assistance Program (EAP)

Your well-being matters to us, and we are committed to providing the support you need, whenever you need it. The EAP provides confidential counselling and support services for various personal and work-related situations or concerns.

Flexible Working

Our culture fosters individual growth within a supportive community, where flexibility isn't just a perk – it's our promise. With a longstanding commitment to work-life balance, we empower you to tailor your schedule to fit your priorities. Whether it's balancing family commitments, pursuing personal passions, or optimizing productivity, our flexible working is here to support your journey.

Inclusive Culture

In our inclusive culture, diversity thrives, fostering an environment where everyone's unique perspectives are valued, supported and respected, empowering individuals to be their best and full selves, every day and collectively solve the world's most complex challenges.

Learning & Development

At Arcadis, our commitment to continuous learning and development is exemplified through our Skills Powered approach, which integrates the innovative programs and resources offered in our Basecamp (Expedition DNA). This holistic strategy cultivates talent, propelling career growth and ensuring our team remains at the forefront of an ever-evolving industry landscape.

Reminder: No Unsolicited Submissions. Direct Applications Only
Please note Arcadis does not accept unsolicited CVs from recruitment agencies and no fees will be paid in relation to such submissions. All candidates must apply directly through our official channels. We appreciate your cooperation and understanding.

Powered by   eightfold.ai #WhatsNextForYou Other Alt Texts in the page: Company logo Powered by Eightfold.ai
```

# jobber_fsm/core/agent/__init__.py

```py

```

# jobber_fsm/core/agent/base.py

```py
import json
from typing import Callable, List, Optional, Tuple, Type

import time
from openai import RateLimitError
import litellm
import openai
from langsmith.wrappers import wrap_openai
from pydantic import BaseModel


from jobber_fsm.utils.function_utils import get_function_schema
from jobber_fsm.utils.logger import logger

# Set global configurations for litellm
litellm.logging = False
litellm.success_callback = ["langsmith"]


class BaseAgent:
    def __init__(
        self,
        name: str,
        system_prompt: str,
        input_format: Type[BaseModel],
        output_format: Type[BaseModel],
        tools: Optional[List[Tuple[Callable, str]]] = None,
        keep_message_history: bool = True,
    ):
        # Metdata
        self.name = name

        # Messages
        self.system_prompt = system_prompt
        self._initialize_messages()
        self.keep_message_history = keep_message_history

        # Input-output format
        self.input_format = input_format
        self.output_format = output_format

        # Llm client
        self.client = wrap_openai(openai.Client())
        # TODO: use lite llm here.
        # self.llm_config = {"model": "gpt-4o-2024-08-06"}

        # Tools
        self.tools_list = []
        self.executable_functions_list = {}
        if tools:
            self._initialize_tools(tools)
            # self.llm_config.update({"tools": self.tools_list, "tool_choice": "auto"})

    def _initialize_tools(self, tools: List[Tuple[Callable, str]]):
        for func, func_desc in tools:
            self.tools_list.append(get_function_schema(func, description=func_desc))
            self.executable_functions_list[func.__name__] = func

    def _initialize_messages(self):
        self.messages = [{"role": "system", "content": self.system_prompt}]

    async def run(self, input_data: BaseModel, screenshot: str = None) -> BaseModel:
        logger.debug(f"[{self.name}] Starting run with input type: {type(input_data).__name__}")
        
        if not isinstance(input_data, self.input_format):
            raise ValueError(f"Input data must be of type {self.input_format.__name__}")

        # Handle message history.
        if not self.keep_message_history:
            self._initialize_messages()

        if screenshot is None:
            self.messages.append(
                {"role": "user", "content": input_data.model_dump_json()}
            )
        else:
            self.messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": input_data.model_dump_json()},
                        {"type": "image_url", "image_url": {"url": f"{screenshot}"}},
                    ],
                }
            )

        logger.debug(f"[{self.name}] Messages to LLM:")
        logger.debug(f"  System prompt length: {len(self.system_prompt)} chars")
        logger.debug(f"  User message: {input_data.model_dump_json()[:200]}...")
        logger.debug(f"  Total messages: {len(self.messages)}")
        logger.debug(f"  Has tools: {len(self.tools_list) > 0}")

        # TODO: add a max_turn here to prevent a inifinite fallout
        while True:
            # TODO:
            # 1. replace this with litellm post structured json is supported.
            # 2. exeception handling while calling the client
            try:
                if len(self.tools_list) == 0:
                    logger.debug(f"[{self.name}] Calling LLM without tools")
                    
                    # Add retry logic for rate limits
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            response = self.client.beta.chat.completions.parse(
                                model="gpt-4o-2024-08-06",
                                messages=self.messages,
                                response_format=self.output_format,
                            )
                            break  # Success, exit retry loop
                        except RateLimitError as e:
                            if attempt < max_retries - 1:
                                wait_time = (attempt + 1) * 5  # 5, 10, 15 seconds
                                logger.warning(f"[{self.name}] Rate limit hit, waiting {wait_time}s before retry...")
                                time.sleep(wait_time)
                            else:
                                raise  # Re-raise on final attempt
                else:
                    logger.debug(f"[{self.name}] Calling LLM with {len(self.tools_list)} tools")
                    
                    # Add retry logic for rate limits
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            response = self.client.beta.chat.completions.parse(
                                model="gpt-4o-2024-08-06",
                                messages=self.messages,
                                response_format=self.output_format,
                                tool_choice="auto",
                                tools=self.tools_list,
                            )
                            break  # Success, exit retry loop
                        except RateLimitError as e:
                            if attempt < max_retries - 1:
                                wait_time = (attempt + 1) * 5  # 5, 10, 15 seconds
                                logger.warning(f"[{self.name}] Rate limit hit, waiting {wait_time}s before retry...")
                                time.sleep(wait_time)
                            else:
                                raise  # Re-raise on final attempt
                
                logger.debug(f"[{self.name}] Got response from LLM")
                response_message = response.choices[0].message
                
                # Log the raw response
                if hasattr(response_message, 'content') and response_message.content:
                    logger.debug(f"[{self.name}] LLM response content: {response_message.content[:500]}...")
                
                tool_calls = response_message.tool_calls

                if tool_calls:
                    logger.debug(f"[{self.name}] LLM made {len(tool_calls)} tool calls")
                    self.messages.append(response_message)
                    for tool_call in tool_calls:
                        await self._append_tool_response(tool_call)
                    continue

                parsed_response_content: self.output_format = response_message.parsed
                
                logger.debug(f"[{self.name}] Parsed response: {parsed_response_content}")
                
                return parsed_response_content
                
            except Exception as e:
                logger.error(f"[{self.name}] Error calling LLM: {e}", exc_info=True)
                raise

    async def _append_tool_response(self, tool_call):
        function_name = tool_call.function.name
        function_to_call = self.executable_functions_list[function_name]
        function_args = json.loads(tool_call.function.arguments)

        logger.debug(f"[{self.name}] Executing tool: {function_name} with args: {function_args}")

        try:
            # ← run the tool
            function_response = await function_to_call(**function_args)

            # ← hand the result back to the LLM
            self.messages.append(
                {
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": str(function_response),
                }
            )

        except Exception as e:
            # log & return the exception string instead of crashing
            logger.warning(f"[Tool Error] {function_name}: {e}")

            self.messages.append(
                {
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": f"The tool raised an error: {e}",
                }
            )
```

# jobber_fsm/core/agent/browser_nav_agent.py

```py
from __future__ import annotations
import json

from typing import List, Tuple, Callable

from jobber_fsm.core.agent.base import BaseAgent
from jobber_fsm.core.models.models import BrowserNavInput, BrowserNavOutput
from jobber_fsm.core.prompts import LLM_PROMPTS
from jobber_fsm.utils.logger import logger

# ──────────────────────────────────────────────────────────────────────────────
# Skills / tools you already had
# ──────────────────────────────────────────────────────────────────────────────
from jobber_fsm.core.skills.open_url import openurl
from jobber_fsm.core.skills.enter_text_and_click import enter_text_and_click
from jobber_fsm.core.skills.get_dom_with_content_type import (
    get_dom_with_content_type,
)
from jobber_fsm.core.skills.click_using_selector import click as click_element
from jobber_fsm.core.skills.enter_text_using_selector import (
    bulk_enter_text,
    entertext,
)
from jobber_fsm.core.skills.get_url import geturl
from jobber_fsm.core.skills.press_key_combination import press_key_combination
from jobber_fsm.core.skills.pdf_text_extractor import extract_text_from_pdf
from jobber_fsm.core.skills.upload_file import upload_file
from jobber_fsm.core.skills.check_login_status import check_login_status

from jobber_fsm.core.memory.credentials import get_credentials, save_credentials, get_default_password

from jobber_fsm.core.models.models import Task
from itertools import count

_task_seq = count(1)   # simple auto-incrementing id per run

def _mk_task(bullet: str) -> Task:
    """
    Convert one markdown-bullet line into a minimal Task object.
    Only *description* is meaningful for the executor right now;
    other required fields are stubbed.
    """
    return Task(
        id       = next(_task_seq),
        description = bullet.lstrip("- ").strip(),
        url      = None,
        result   = "",
    )


class BrowserNavAgent(BaseAgent):
    """
    Executes the low-level browser steps generated by the planner.

    `auto_mode=True`  – never stop to ask the human  
    `auto_mode=False` – may request clarification interactively
    """

    def __init__(self, planner, *, auto_mode: bool = False) -> None:
        self.planner = planner
        self.auto_mode = auto_mode
        self.screenshot_debugger = None  # Will be set by cloud_job_runner

        super().__init__(
            name="executor",
            system_prompt=LLM_PROMPTS["BROWSER_AGENT_PROMPT"],
            input_format=BrowserNavInput,
            output_format=BrowserNavOutput,
            keep_message_history=auto_mode,
            tools=self._tool_specs(),
        )

    # ------------------------------------------------------------------ #
    # public entry-point – called by PlannerAgent
    # ------------------------------------------------------------------ #
    async def process_query(self, task_text: str) -> BrowserNavOutput:
        logger.debug("BrowserNavAgent: executing task -> %s", task_text)

        # Helper function to take screenshots
        async def take_screenshot(name: str, description: str):
            if self.screenshot_debugger:
                try:
                    from jobber_fsm.core.web_driver.playwright import PlaywrightManager
                    browser_manager = PlaywrightManager()
                    page = await browser_manager.get_current_page()
                    if page:
                        logger.info(f"[Screenshot] Taking screenshot: {name}")
                        path = await self.screenshot_debugger.capture(page, name, description)
                        if path:
                            logger.info(f"[Screenshot] Saved: {path}")
                        else:
                            logger.warning(f"[Screenshot] Failed to save: {name}")
                    else:
                        logger.warning("[Screenshot] No page available")
                except Exception as e:
                    logger.error(f"[Screenshot] Error: {e}", exc_info=True)

        # Take pre-task screenshot - remove the completed_tasks reference
        await take_screenshot(f"task_start", f"Starting: {task_text}")

        llm_reply: BrowserNavOutput = await self.run(
            BrowserNavInput(task=_mk_task(task_text))
        )

        if getattr(llm_reply, "tool_calls", None):
            for i, call in enumerate(llm_reply.tool_calls):
                # Take pre-tool screenshot
                await take_screenshot(f"before_{call.function.name}_{i}", f"Before {call.function.name}")
                
                msg = await self._apply_tool(call)
                logger.info("[tool-result] %s → %s", call.function.name, msg)
                
                # Take post-tool screenshot
                await take_screenshot(f"after_{call.function.name}_{i}", f"Result: {msg[:100]}")

        # Take post-task screenshot
        await take_screenshot(f"task_end", f"Completed: {task_text}")

        if getattr(llm_reply, "content", None) is None:
            llm_reply.content = f"[dry-run] would execute: {task_text}"

        logger.info("[dry-run] would perform ⇒ %s", llm_reply.model_dump_json(indent=2))

        return llm_reply
    
    async def check_for_security_page(page) -> bool:
        """Check if we're on a security/verification page"""
        try:
            url = page.url.lower()
            title = await page.title()
            
            # Common indicators of security pages
            security_indicators = [
                'security-check',
                'verify',
                'captcha',
                'challenge',
                'bot-check',
                'human-check',
                'access-denied'
            ]
            
            # Check URL and title
            for indicator in security_indicators:
                if indicator in url or indicator in title.lower():
                    return True
                    
            # Check page content
            try:
                content = await page.content()
                if any(phrase in content.lower() for phrase in [
                    'verify you are human',
                    'security check',
                    'captcha',
                    'cloudflare',
                    'checking your browser'
                ]):
                    return True
            except:
                pass
                
            return False
        except Exception as e:
            logger.error(f"Error checking for security page: {e}")
            return False
    
    async def _apply_tool(self, tool_call) -> str:
        """Given one `tool_call` from the LLM, invoke the matching skill."""
        fn_name = tool_call.function.name
        args    = json.loads(tool_call.function.arguments)
        func    = self.executable_functions_list[fn_name]
        return await func(**args)

    # ------------------------------------------------------------------ #
    # internal helpers
    # ------------------------------------------------------------------ #
    def _tool_specs(self) -> List[Tuple[Callable, str]]:
        """Return the list of (function, prompt-snippet) tuples for tools."""
        return [
            (openurl,                    LLM_PROMPTS["OPEN_URL_PROMPT"]),
            (enter_text_and_click,       LLM_PROMPTS["ENTER_TEXT_AND_CLICK_PROMPT"]),
            (get_dom_with_content_type,  LLM_PROMPTS["GET_DOM_WITH_CONTENT_TYPE_PROMPT"]),
            (click_element,              LLM_PROMPTS["CLICK_PROMPT"]),
            (geturl,                     LLM_PROMPTS["GET_URL_PROMPT"]),
            (bulk_enter_text,            LLM_PROMPTS["BULK_ENTER_TEXT_PROMPT"]),
            (entertext,                  LLM_PROMPTS["ENTER_TEXT_PROMPT"]),
            (press_key_combination,      LLM_PROMPTS["PRESS_KEY_COMBINATION_PROMPT"]),
            (extract_text_from_pdf,      LLM_PROMPTS["EXTRACT_TEXT_FROM_PDF_PROMPT"]),
            (upload_file,                LLM_PROMPTS["UPLOAD_FILE_PROMPT"]),
            (check_login_status,         "Check if the user is currently logged into the website by looking for login/logout indicators"),
        ]

```

# jobber_fsm/core/agent/planner_agent.py

```py
# jobber_fsm/core/agent/planner_agent.py
from __future__ import annotations

import datetime, re
from string import Template
from typing import List, Optional

from jobber_fsm.core.agent.base import BaseAgent
from jobber_fsm.core.agent.browser_nav_agent import BrowserNavAgent
from jobber_fsm.core.memory import ltm
from jobber_fsm.core.prompts import LLM_PROMPTS
from jobber_fsm.core.skills.get_screenshot import get_screenshot
from jobber_fsm.core.models.models import (
    PlannerInput,
    PlannerOutput,
    Task,
)
from jobber_fsm.utils.logger import logger


class PlannerAgent(BaseAgent):
    """
    Turns a high-level *objective* into an ordered list of browser tasks.

    • `auto_mode=True`  – run fully autonomously  
    • `auto_mode=False` – may ask the user for clarifications
    """

    def __init__(self, *, auto_mode: bool = False) -> None:
        self.auto_mode = auto_mode

        # ── build SYSTEM prompt ────────────────────────────────────────────
        # First, let's see what's in the job context
        job_context = ltm.get_job_apply_context()
        logger.debug(f"[planner] Job context loaded: {job_context}")
        
        user_ltm = "\n" + (ltm.get_user_ltm() or "")
        
        # Include job context in the prompt
        if job_context:
            user_ltm += f"\n\nJob Application Context:\n{job_context}"
        
        system_prompt = Template(LLM_PROMPTS["PLANNER_AGENT_PROMPT"]).substitute(
            basic_user_information=user_ltm
        )

        today = datetime.datetime.now()
        system_prompt += f"\nToday's date is: {today:%d/%m/%Y}"
        system_prompt += f"\nCurrent weekday is: {today:%A}"
        
        logger.debug(f"[planner] System prompt length: {len(system_prompt)} chars")
        logger.debug(f"[planner] System prompt preview: {system_prompt[:500]}...")

        # ── boot BaseAgent ─────────────────────────────────────────────────
        super().__init__(
            name="planner",
            system_prompt=system_prompt,
            input_format=PlannerInput,
            output_format=PlannerOutput,
            keep_message_history=auto_mode,
        )

        # low-level executor
        self.browser_agent = BrowserNavAgent(self, auto_mode=auto_mode)

    # ------------------------------------------------------------------ #
    # internal helper – create a best-effort Task when LLM forgot one
    # ------------------------------------------------------------------ #
    def _fallback_next_task(
        self,
        plan: Optional[List[Task]],
        completed: Optional[List[Task]],
        content: Optional[str],
    ) -> Optional[Task]:
        """Heuristic: first incomplete task in *plan*, else first bullet in *content*."""
        if plan:
            done_ids = {t.id for t in (completed or [])}
            for t in plan:
                if t.id not in done_ids:
                    logger.debug("[planner] fallback picked next_task id=%s from plan", t.id)
                    return t

        if content:
            bullets = re.findall(r"^\s*-\s+(.*)", content, flags=re.MULTILINE)
            if bullets:
                logger.debug("[planner] fallback created next_task from bullet text")
                return Task(id=999_999, description=bullets[0], url=None, result="")

        return None  # could not recover

    # ------------------------------------------------------------------ #
    # main loop – called from runner.py
    # ------------------------------------------------------------------ #
    async def process_query(self, inp: PlannerInput) -> PlannerOutput:
        """
        • receives `PlannerInput` from the orchestrator/runner  
        • iteratively refines the plan and delegates tasks to the BrowserNavAgent  
        • returns when `is_complete == True`
        """
        logger.info(f"[planner] Processing query with objective: {inp.objective}")
        logger.debug(f"[planner] Input data: {inp.model_dump_json(indent=2)}")
        
        # Get initial response from LLM
        logger.info("[planner] Getting initial plan from LLM...")
        response: PlannerOutput = await self.run(inp)
        
        logger.info(f"[planner] Initial LLM response received:")
        logger.info(f"  - is_complete: {response.is_complete}")
        logger.info(f"  - has plan: {response.plan is not None}")
        logger.info(f"  - has next_task: {response.next_task is not None}")
        logger.info(f"  - final_response: {response.final_response}")
        
        if response.plan:
            logger.info(f"[planner] Plan has {len(response.plan)} tasks:")
            for i, task in enumerate(response.plan):
                logger.info(f"  {i+1}. {task.description}")
        
        # Keep track of completed tasks locally
        completed_tasks: List[Task] = inp.completed_tasks or []
        iteration = 0

        while True:
            iteration += 1
            logger.debug(f"[planner] Iteration {iteration}")
            
            if response.is_complete:
                logger.info("[planner] Plan marked as complete!")
                return response

            # ----------------------------------------------------------------
            # obtain the next task, tolerating occasional LLM omissions
            # ----------------------------------------------------------------
            next_task = response.next_task
            if next_task is None:
                logger.warning("[planner] No next_task provided, attempting fallback...")
                next_task = self._fallback_next_task(
                    response.plan, completed_tasks, response.final_response
                )
                if next_task is None:
                    logger.error("[planner] Could not determine next task!")
                    raise RuntimeError(
                        "Planner returned is_complete=False without next_task "
                        "and fallback recovery failed."
                    )
                logger.warning("[planner] recovered missing next_task: %s", next_task)

            # delegate next task to executor
            logger.info(f"[planner] Delegating task to browser agent: {next_task.description}")
            nav_out = await self.browser_agent.process_query(next_task.description)
            logger.info(f"[planner] Browser agent completed task")

            # ---- update completed tasks list ---------------------
            if nav_out.completed_task:
                completed_tasks.append(nav_out.completed_task)
                logger.info(f"[planner] Total completed tasks: {len(completed_tasks)}")

            # ---- build next PlannerInput for the loop ---------------------
            inp = PlannerInput(
                objective=inp.objective,
                plan=response.plan,
                completed_tasks=completed_tasks,
                task_for_review=nav_out.completed_task,
            )

            # ---- get next step from LLM ----------------------------------
            logger.info("[planner] Getting next step from LLM...")
            response = await self.run(inp)
            
            logger.debug(f"[planner] LLM response for iteration {iteration}:")
            logger.debug(f"  - is_complete: {response.is_complete}")
            logger.debug(f"  - has next_task: {response.next_task is not None}")

    # ------------------------------------------------------------------ #
    # message from BrowserNavAgent (incl. screenshot)
    # ------------------------------------------------------------------ #
    async def receive_browser_message(self, message: str):
        screenshot_url = await get_screenshot()

        return await self.generate_reply(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Helper response: {message}\n"
                                "Here is a screenshot of the current browser page."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": screenshot_url},
                        },
                    ],
                }
            ],
            self.browser_agent,
        )
```

# jobber_fsm/core/memory/__init__.py

```py

```

# jobber_fsm/core/memory/credentials.py

```py
"""
Credential management for job sites
"""
import json
import os
from pathlib import Path
from typing import Dict, Optional
from jobber_fsm.utils.logger import logger

CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"

def get_default_password(first_name: str) -> str:
    """Generate a consistent password for a given first name"""
    return f"{first_name}Job2025!"

def save_credentials(site_domain: str, email: str, password: str) -> None:
    """Save credentials for a job site"""
    credentials = load_all_credentials()
    credentials[site_domain] = {
        "email": email,
        "password": password,
        "created_at": str(Path.ctime(Path.cwd()))
    }
    
    with CREDENTIALS_FILE.open('w') as f:
        json.dump(credentials, f, indent=2)
    
    logger.info(f"Saved credentials for {site_domain}")

def get_credentials(site_domain: str) -> Optional[Dict[str, str]]:
    """Get saved credentials for a job site"""
    credentials = load_all_credentials()
    return credentials.get(site_domain)

def load_all_credentials() -> Dict[str, Dict[str, str]]:
    """Load all saved credentials"""
    if not CREDENTIALS_FILE.exists():
        return {}
    
    try:
        with CREDENTIALS_FILE.open() as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading credentials: {e}")
        return {}
```

# jobber_fsm/core/memory/job_apply_context.json

```json
{
  "url": "https://jobs.arcadis.com/careers/job/563671521052060-senior-project-manager-and-technical-consultant-toronto-on-canada",
  "profile": {
    "first_name": "Alice",
    "last_name": "Ng",
    "email": "alice@example.com",
    "phone": "+1-555-123-4567",
    "linkedin_profile": "https://linkedin.com/in/aliceng",
    "date_of_birth": "1997-04-03",
    "gender": "Female",
    "race_ethnicity": "Asian",
    "veteran_status": "No",
    "disability_status": "No",
    "visa_countries": [
      {
        "code": "US",
        "name": "United States",
        "requiresSponsorship": false
      }
    ],
    "resume_path": "/absolute/path/AliceNgResume.pdf"
  }
}
```

# jobber_fsm/core/memory/ltm.py

```py
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

```

# jobber_fsm/core/models/__init__.py

```py

```

# jobber_fsm/core/models/models.py

```py
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


# Global
class State(str, Enum):
    PLAN = "plan"
    BROWSE = "browse"
    COMPLETED = "completed"


class Task(BaseModel):
    id: int
    description: str
    url: Optional[str]
    result: Optional[str]


class Memory(BaseModel):
    objective: str
    current_state: State
    plan: Optional[List[Task]]
    completed_tasks: Optional[List[Task]]
    current_task: Optional[List[Task]]
    final_response: Optional[str]

    class Config:
        use_enum_values = True


# Planner
class PlannerInput(BaseModel):
    objective: str
    plan: Optional[List[Task]]
    completed_tasks: Optional[List[Task]]
    task_for_review: Optional[Task]


class PlannerOutput(BaseModel):
    plan: Optional[List[Task]]
    next_task: Optional[Task]
    is_complete: bool
    final_response: Optional[str]


# Executor
class BrowserNavInput(BaseModel):
    task: Task


class BrowserNavOutput(BaseModel):
    # Remove the 'terminate' field and align with what the code expects
    completed_task: Task
    # You can keep additional fields if needed
    content: Optional[str] = None

```

# jobber_fsm/core/models/planner_io.py

```py
from pydantic import BaseModel

class PlannerInput(BaseModel):
    query: str        # free-form directive from orchestrator

class PlannerOutput(BaseModel):
    terminate: bool   # True when planning is done
    content: str      # plan (or final answer) in plain text or JSON

```

# jobber_fsm/core/orchestrator/orchestrator.py

```py
import asyncio
import textwrap
from typing import Dict

from colorama import Fore, init
from dotenv import load_dotenv

from jobber_fsm.core.agent.base import BaseAgent
from jobber_fsm.core.models.models import (
    BrowserNavInput,
    BrowserNavOutput,
    Memory,
    PlannerInput,
    PlannerOutput,
    State,
    Task,
)
from jobber_fsm.core.skills.get_screenshot import get_screenshot
from jobber_fsm.core.skills.get_url import geturl
from jobber_fsm.core.web_driver.playwright import PlaywrightManager
from jobber_fsm.utils.logger import logger

init(autoreset=True)


class Orchestrator:
    def __init__(
        self, state_to_agent_map: Dict[State, BaseAgent], eval_mode: bool = False
    ):
        load_dotenv()
        self.state_to_agent_map = state_to_agent_map
        self.playwright_manager = PlaywrightManager()
        self.eval_mode = eval_mode
        self.shutdown_event = asyncio.Event()
    
    async def _bootstrap(self) -> None:
        """
        Bring the FSM + browser up **without** entering the interactive
        input-loop.  Upstream Jobber calls the same thing from `start()`
        before dropping into `while True: input(...)`.
        """
        if getattr(self, "_booted", False):
            return                      # idempotent

        logger.info("[Orchestrator] Starting bootstrap")
        
        # ---- create the Playwright manager + context -------------
        logger.info("[Orchestrator] About to initialize PlaywrightManager")
        await self.playwright_manager.async_initialize(eval_mode=False)
        logger.info("[Orchestrator] PlaywrightManager initialized successfully")

        # ---- whatever the original `start()` does *before* the REPL
        self.current_state = list(self.state_to_agent_map.keys())[0]
        self._booted = True
        logger.info("[Orchestrator] Bootstrap completed")

    async def start(self):
        print("Starting orchestrator")
        await self.playwright_manager.async_initialize(eval_mode=self.eval_mode)
        print("Browser started and ready")

        if not self.eval_mode:
            await self._command_loop()

    async def _command_loop(self):
        while not self.shutdown_event.is_set():
            try:
                command = await self._get_user_input()
                if command.strip().lower() == "exit":
                    await self.shutdown()
                else:
                    await self.execute_command(command)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"An error occurred: {e}")

    async def _get_user_input(self):
        return await asyncio.get_event_loop().run_in_executor(
            None, input, "Enter your command (or type 'exit' to quit) "
        )

    async def execute_command(self, command: str):
        try:
            # Create initial memory
            self.memory = Memory(
                objective=command,
                current_state=State.PLAN,
                plan=[],
                completed_tasks=[],
                current_task=None,
                final_response=None,
            )
            print(f"Executing command {self.memory.objective}")
            while self.memory.current_state != State.COMPLETED:
                await self._handle_state()
            self._print_final_response()

            if self.eval_mode:
                return self.memory.final_response
            else:
                return
        except Exception as e:
            print(f"Error executing the command {self.memory.objective}: {e}")

    def run(self) -> Memory:
        while self.memory.current_state != State.COMPLETED:
            self._handle_state()

        self._print_final_response()
        return self.memory
    
    async def start_auto(self) -> dict:
        """Run the FSM until it reaches State.DONE and return the final memory."""
        await self.start(initial_command="start")   # bypass REPL
        return self.memory  # or whatever structure you store results in

    async def _handle_state(self):
        current_state = self.memory.current_state

        if current_state not in self.state_to_agent_map:
            raise ValueError(f"Unhandled state! No agent for {current_state}")

        if current_state == State.PLAN:
            await self._handle_planner()
        elif current_state == State.BROWSE:
            await self._handle_browser_navigation()
        else:
            raise ValueError(f"Unhandled state: {current_state}")

    async def _handle_planner(self):
        agent = self.state_to_agent_map[State.PLAN]
        self._print_memory_and_agent(agent.name)

        screenshot = await get_screenshot()

        input_data = PlannerInput(
            objective=self.memory.objective,
            # plan=self.memory.plan,
            plan=None,
            task_for_review=self.memory.current_task,
            completed_tasks=self.memory.completed_tasks,
        )

        output: PlannerOutput = await agent.run(input_data, screenshot)

        self._update_memory_from_planner(output)

        print(f"{Fore.MAGENTA}Planner has updated the memory.")

    async def _handle_browser_navigation(self):
        agent = self.state_to_agent_map[State.BROWSE]
        self._print_memory_and_agent(agent.name)

        # Update task with url
        current_task: Task = self.memory.current_task
        current_task.url = await geturl()

        input_data = BrowserNavInput(task=current_task)

        output: BrowserNavOutput = await agent.run(input_data)

        self._print_task_result(output.completed_task)

        self._update_memory_from_browser_nav(output)

        print(f"{Fore.MAGENTA}Executor has completed a task.")

    def _update_memory_from_planner(self, planner_output: PlannerOutput):
        if planner_output.is_complete:
            self.memory.current_state = State.COMPLETED
            self.memory.final_response = planner_output.final_response
        elif planner_output.next_task:
            self.memory.current_state = State.BROWSE
            self.memory.plan = planner_output.plan
            next_task_id = len(self.memory.completed_tasks) + 1
            self.memory.current_task = Task(
                id=next_task_id,
                description=planner_output.next_task.description,
                url=None,
                result=None,
            )
        else:
            raise ValueError("Planner did not provide next task or completion status")

    def _update_memory_from_browser_nav(self, browser_nav_output: BrowserNavOutput):
        self.memory.completed_tasks.append(browser_nav_output.completed_task)
        self.memory.current_task = None
        self.memory.current_state = State.PLAN

    async def shutdown(self):
        print("Shutting down orchestrator!")
        self.shutdown_event.set()
        await self.playwright_manager.stop_playwright()

    def _print_memory_and_agent(self, agent_type: str):
        print(f"{Fore.CYAN}{'='*50}")
        print(f"{Fore.YELLOW}Current State: {Fore.GREEN}{self.memory.current_state}")
        print(f"{Fore.YELLOW}Agent: {Fore.GREEN}{agent_type}")
        if len(self.memory.plan) == 0:
            print(f"{Fore.YELLOW}Plan:{Fore.GREEN} none")
        else:
            print(f"{Fore.YELLOW}Plan:")
            for task in self.memory.plan:
                print(f"{Fore.GREEN} {task.id}. {task.description}")
        if self.memory.current_task:
            print(
                f"{Fore.YELLOW}Current Task: {Fore.GREEN}{self.memory.current_task.description}"
            )
        if len(self.memory.completed_tasks) == 0:
            print(f"{Fore.YELLOW}Completed Tasks:{Fore.GREEN} none")
        else:
            print(f"{Fore.YELLOW}Completed Tasks:")
            for task in self.memory.completed_tasks:
                status = "✓" if task.result else " "
                print(f"{Fore.GREEN}  [{status}] {task.id}. {task.description}")
        print(f"{Fore.CYAN}{'='*50}")

    def _print_task_result(self, task: Task):
        print(f"{Fore.CYAN}{'='*50}")
        print(f"{Fore.YELLOW}Task Completed: {Fore.GREEN}{task.description}")
        print(f"{Fore.YELLOW}Result:")
        wrapped_result = textwrap.wrap(task.result, width=80)
        for line in wrapped_result:
            print(f"{Fore.WHITE}{line}")
        print(f"{Fore.CYAN}{'='*50}")

    def _print_final_response(self):
        print(f"\n{Fore.GREEN}{'='*50}")
        print(f"{Fore.GREEN}Objective Completed!")
        print(f"{Fore.GREEN}{'='*50}")
        print(f"{Fore.YELLOW}Final Response:")
        wrapped_response = textwrap.wrap(self.memory.final_response, width=80)
        for line in wrapped_response:
            print(f"{Fore.WHITE}{line}")
        print(f"{Fore.GREEN}{'='*50}")

```

# jobber_fsm/core/prompts/__init__.py

```py
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

```

# jobber_fsm/core/prompts/planner_agent_prompt.txt

```txt
You are **Jobber-Planner**, a specialised agent that breaks a job-application mission into explicit browser steps for a separate Executor agent.

Your capabilities include:
1. Creating new accounts on job platforms
2. Logging into existing accounts
3. Filling out job application forms
4. Uploading resumes and documents
5. Handling multi-step application processes

────────────────────────
🔎  CONTEXT AVAILABLE TO YOU
────────────────────────
- The variable  ${basic_user_information}  (already inserted further below)
  – It contains a JSON blob with:
    {
      "url": "<target-apply-link>",
      "profile": {
        "firstName": …,
        "lastName": …,
        "email": …,
        "phone": …,
        "linkedinProfile": …,
        "dateOfBirth": …,
        "gender": …,
        "raceEthnicity": …,
        "protectedVeteran": …,
        "disabilityStatus": …,
        "visaCountries": [ {"code": "US", "name": "United States", "requiresSponsorship": false }, … ],
        "cvFilename": …,
        "cvUri": …      // presigned URL you can paste into <input type=file>
      }
    }

────────────────────────
📋  ACCOUNT CREATION STRATEGY
────────────────────────
When encountering a job site that requires login:
1. First check if there's a "Sign In" or "Login" option
2. If not logged in, look for "Create Account" or "Sign Up"
3. For account creation, use the profile email and create a consistent password
4. Default password pattern: FirstName + "Job2025!" (e.g., "AliceJob2025!")
5. Fill all required fields using profile data
6. Handle email verification by informing the user if manual verification is needed
7. Save login state for future applications

────────────────────────
📋  HOW TO USE EACH FIELD
────────────────────────
| Profile key          | Typical form element / purpose                                             |
|----------------------|-----------------------------------------------------------------------------|
| firstName            | "First name" / "Given name" text input                                     |
| lastName             | "Last name" / "Family name" text input                                     |
| email                | • Sign-up / log-in email <br>• "Contact e-mail" field                      |
| phone                | "Phone", "Mobile", or "Cell" number input                                   |
| linkedinProfile      | Paste into fields labelled: "LinkedIn", "Professional profile", "Website"   |
| dateOfBirth          | Date picker or three separate DD-MM-YYYY dropdowns                          |
| gender               | Radio/group select labelled "Gender", "Sex", or "Pronouns" (if present)     |
| raceEthnicity        | Equal-opportunity questionnaire radio / multi-select                        |
| protectedVeteran     | Yes/No radio for veteran status                                             |
| disabilityStatus     | Yes/No radio (may be phrased "Do you have a disability?")                   |
| visaCountries        | Work-authorisation questions <br>• If job_country ∈ visaCountries → answer "Yes, authorised" <br>• If requiresSponsorship==true for that country → answer "Need sponsorship" |
| cvFilename + cvUri   | Upload résumé file when an <input type=file> or "Attach resume" button appears |

────────────────────────
🗂️  STRUCTURED OUTPUT YOU MUST PRODUCE
────────────────────────
Return **JSON** with these top-level keys **exactly**:

{
  "plan": [
    {"id": 1, "description": "Task description", "url": null, "result": null}
  ],
  "next_task": {"id": 1, "description": "Task description", "url": null, "result": null},
  "is_complete": false,
  "final_response": null
}

**Never** ask the human for missing data – use only what is in the profile.

────────────────────────
💡  TASK BREAKDOWN EXAMPLES
────────────────────────
Example flow for a job application:
1. Navigate to job posting URL
2. Check if logged in (look for profile menu, logout button, or user name)
3. If not logged in:
   a. Click "Sign In" or "Login"
   b. Look for "Create Account" or "Sign Up" link
   c. Click on account creation option
   d. Fill registration form with profile data
   e. Submit registration
   f. Note if email verification is required
4. Once logged in, proceed with application:
   a. Click "Apply" button
   b. Fill application form fields
   c. Upload resume
   d. Answer screening questions
   e. Submit application

────────────────────────
💾  USER LTM
────────────────────────
${basic_user_information}

────────────────────────
🚨  HANDLING SECURITY CHECKS
────────────────────────
If you encounter a security check, CAPTCHA, or "verify you're human" page:
1. First, check the page content to understand what type of verification is required
2. If it's a simple checkbox (like "I'm not a robot"), try clicking it
3. If it's a CAPTCHA or complex verification:
   - Report that manual intervention is required
   - Mark the task as complete with a message explaining the security check
   - Do NOT keep retrying the same actions
```

# jobber_fsm/core/prompts/prompts.py

```py
LLM_PROMPTS = {
    "USER_AGENT_PROMPT": """A proxy for the user for executing the user commands.""",
    "BROWSER_NAV_EXECUTOR_PROMPT": """A proxy for the user for executing the user commands.""",
    "PLANNER_AGENT_PROMPT": """You are a web automation task planner. You will receive tasks from the user and will work with a naive AI Helper agent to accomplish it.
    You will think step by step and break down the tasks into sequence of simple tasks. Tasks will be delegated to the Helper to execute on browser. 
    
    Your input and output will strictly be a well-fromatted JSON with attributes as mentioned below. 

    Input:
    - objective: Mandatory string representing the main objective to be achieved via web automation
    - plan: Optional list of tasks representing the plan. If the plan is provided, use it to figure out the next task or modify the plan as per your need to achieve the objective
    - task_for_review: Optional object representing recently completed task (if any) from Helper agent that needs to be reviewed.
    - completed_tasks: Optional list of all tasks that have been completed so far by the Helper agent in order to complete the objectiv.

    Output:
    - plan: Mandaory List of tasks that need be performed to achieve the objective. Update this based on the objective, completed_tasks, tasks_for_review. You will also be provided with the current screenhot of the browser page by the Helper to plan better. Your END goal is to achieve objective. 
    - next_task: Optional String representing detailed next task to be executed by Helper agent(if the objective is not yet complete). Next task is consistent with the plan. This needs to be present for every response except when objective has been achieved. Once you recieve a confirmation from that your previous task HAS BEEN EXECUTED, SEND THE next_task from the OVERALL plan. MAKE SURE to look at the provided screenshot to adjust the appropriate next task
    - is_complete: Mandatory boolean indicating whether the entire objective has been achieved. Return True when the exact objective is complete without any compromises or you are absolutely convinced that the objective cannot be completed, no otherwise. This is mandatory for every response.
    - final_response: Optional string representing the summary of the completed work. This is to be returned only if the objective is COMPLETE. This is the final answer string that will be returned to the user. Use the plan and result to come with final response for the objective provided by the user.

    Format of Task String: 
    - id: Mandatory Integer representing the id of the task
    - description: Mandatory string representing the description of the task


    Capabilities and limitation of the AI Helper agent:
    1. Helper can navigate to urls, perform simple interactions on a page or answer any question you may have about the current page.
    2. Helper cannot perform complex planning, reasoning or analysis. You will not delegate any such tasks to helper, instead you will perform them based on information from the helper.
    3. Helper is stateless and treats each step as a new task. Helper will not remember previous pages or actions. So, you will provide all necessary information as part of each step.
    4. Very Important: Helper cannot go back to previous pages. If you need the helper to return to a previous page, you must explicitly add the URL of the previous page in the step (e.g. return to the search result page by navigating to the url https://www.google.com/search?q=Finland")

    Guidelines:
    1. If you know the direct URL, use it directly instead of searching for it (e.g. go to www.espn.com). Optimise the plan to avoid unnecessary steps.
    2. Do not assume any capability exists on the webpage. Ask questions to the helper to confirm the presence of features (e.g. is there a sort by price feature available on the page?). This will help you revise the plan as needed and also establish common ground with the helper.
    3. Do not combine multiple steps into one. A step should be strictly as simple as interacting with a single element or navigating to a page. If you need to interact with multiple elements or perform multiple actions, you will break it down into multiple steps. ## Important - This pointer is not true for filling out forms. Helper has the ability to fill multiple form fileds in one shot. Send appropriate instructions for multiple fields that you see for helper to fill out. ##
    4. Important: You will NOT ask for any URLs of hyperlinks in the page from the helper, instead you will simply ask the helper to click on specific result. URL of the current page will be automatically provided to you with each helper response.
    5. Very Important: Add verification as part of the plan, after each step and specifically before terminating to ensure that the task is completed successfully. Use the provided screenshot to verify that the helper is completeing each step successfully as directed. If not, modify the plan accordingly.
    6. If the task requires multiple informations, all of them are equally important and should be gathered before terminating the task. You will strive to meet all the requirements of the task.
    7. If one plan fails, you MUST revise the plan and try a different approach. You will NOT terminate a task untill you are absolutely convinced that the task is impossible to accomplish.
    8. Do NOT confirm if a file has been uploaded or not. 
    9. Do NOT blindly trust what the helper agent says in its response. ALWAYS look at the provided image to confirm if the task has actually been done properly by the helper. Use the screenshot as the GROUND TRUTH to understand where you are, if the task was done or not and how can you move towards achieveing the overall objective. 
    10. Re-confirm once more, look at the screenshot carefully and think critically if the task has been actually acheieved before doing the final termination. 

    Complexities of web navigation:
    1. Many forms have mandatory fields that need to be filled up before they can be submitted. Ask the helper for what fields look mandatory.
    2. In many websites, there are multiple options to filter or sort results. Ask the helper to list any  elements on the page which will help the task (e.g. are there any links or interactive elements that may lead me to the support page?).
    3. Always keep in mind complexities such as filtering, advanced search, sorting, and other features that may be present on the website. Ask the helper whether these features are available on the page when relevant and use them when the task requires it.
    4. Very often list of items such as, search results, list of products, list of reviews, list of people etc. may be divided into multiple pages. If you need complete information, it is critical to explicitly ask the helper to go through all the pages.
    5. Sometimes search capabilities available on the page will not yield the optimal results. Revise the search query to either more specific or more generic.
    6. When a page refreshes or navigates to a new page, information entered in the previous page may be lost. Check that the information needs to be re-entered (e.g. what are the values in source and destination on the page?).
    7. Sometimes some elements may not be visible or be disabled until some other action is performed. Ask the helper to confirm if there are any other fields that may need to be interacted for elements to appear or be enabled.

    Example 1:
    Input: {
      "objective": "Find the cheapest premium economy flights from Helsinki to Stockholm on 15 March on Skyscanner."
    }
    Example Output (when onjective is not yet complete)
    {
    "plan": [
        {"id": 1, "description": "Go to www.skyscanner.com", "url": "https://www.skyscanner.com"},
        {"id": 2, "description": "List the interaction options available on skyscanner page relevant for flight reservation along with their default values"},
        {"id": 3, "description": "Select the journey option to one-way (if not default)"},
        {"id": 4, "description": "Set number of passengers to 1 (if not default)"},
        {"id": 5, "description": "Set the departure date to 15 March 2025"},
        {"id": 6, "description": "Set ticket type to Economy Premium"},
        {"id": 7, "description": "Set from airport to 'Helsinki'"},
        {"id": 8, "description": "Set destination airport to Stockholm"},
        {"id": 9, "description": "Confirm that current values in the source airport, destination airport and departure date fields are Helsinki, Stockholm and 15 March 2025 respectively"},
        {"id": 10, "description": "Click on the search button to get the search results"},
        {"id": 11, "description": "Confirm that you are on the search results page"},
        {"id": 12, "description": "Extract the price of the cheapest flight from Helsinki to Stockholm from the search results"}
    ],
    "next_task": {"id": 1, "description": "Go to www.skyscanner.com", "result": None},
    "is_complete": False,
    }

    # Example Output (when onjective is complete)
    {
    "plan": [...],  # Same as above
    "next_task": None,
    "is_complete": True,
    "final_response": "The cheapest premium economy flight from Helsinki to Stockholm on 15 March 2025 is <flight details>."
    }


    Notice above how there is confirmation after each step and how interaction (e.g. setting source and destination) with each element is a seperate step. Follow same pattern.
    Remember: you are a very very persistent planner who will try every possible strategy to accomplish the task perfectly.
    Revise search query if needed, ask for more information if needed, and always verify the results before terminating the task.
    Some basic information about the user: $basic_user_information""",
    "BROWSER_AGENT_PROMPT": """
    You will perform web navigation tasks, which may include logging into websites and interacting with any web content using the functions made available to you.

    class BrowserNavInput(BaseModel):
    task: Task


class BrowserNavOutput(BaseModel):
    completed_task: Task

    Input: 
    task - Task object represening the task to be completed 

    Output: 
    completed_task: Task object representing the completed task 

    Format of task object: 
    - id: Mandatory Integer representing the id of the task
    - description: Mandatory string representing the description of the task
    - url: Mandary String representing the URL on which task needs to be performed 
    - result: String representing the result of the task. It should be a short summary of the actions you performed to accomplish the task, and what worked and what did not.

    
    Use the provided DOM representation for element location or text summarization.
    Interact with pages using only the "mmid" attribute in DOM elements. 
    ## VERY IMPORTANT - "mmid" wil ALWAYS be a number. 
    ## You must extract mmid value from the fetched DOM, do not conjure it up. 
    ##  VERY IMPORTANT - for any tool which needs "mmid" - make sure you have called the get DOM content tool. You will get MMID only after that 
    ## Execute function sequentially to avoid navigation timing issues. 
    The given actions are NOT parallelizable. They are intended for sequential execution.
    If you need to call multiple functions in a task step, call one function at a time. Wait for the function's response before invoking the next function. This is important to avoid collision.
    Strictly for search fields, submit the field by pressing Enter key. For other forms, click on the submit button.
    Unless otherwise specified, the task must be performed on the current page. Use openurl only when explicitly instructed to navigate to a new page with a url specified. If you do not know the URL ask for it.
    You will NOT provide any URLs of links on webpage. If user asks for URLs, you will instead provide the text of the hyperlink on the page and offer to click on it. This is very very important.
    When inputing information, remember to follow the format of the input field. For example, if the input field is a date field, you will enter the date in the correct format (e.g. YYYY-MM-DD), you may get clues from the placeholder text in the input field.
    if the task is ambigous or there are multiple options to choose from, you will ask the user for clarification. You will not make any assumptions.
    Individual function will reply with action success and if any changes were observed as a consequence. Adjust your approach based on this feedback.
    Once the task is completed or cannot be completed, return a short summary of the actions you performed to accomplish the task, and what worked and what did not. Your reply will not contain any other information.
    Additionally, If task requires an answer, you will also provide a short and precise answer in the result. 
    Ensure that user questions are answered from the DOM and not from memory or assumptions. To answer a question about textual information on the page, prefer to use text_only DOM type. To answer a question about interactive elements, use all_fields DOM type.
    Do not provide any mmid values in your response.
    Important: If you encounter an issues or is unsure how to proceed, return & provide a detailed summary of the exact issue encountered.
    Do not repeat the same action multiple times if it fails. Instead, if something did not work after a few attempts, terminate the task.
    
    ## SOME VERY IMPORTANT POINTS TO ALWAYS REMEMBER ##
    2. NEVER ASK WHAT TO DO NEXT  or HOW would they like to proceed to the user. 
    3. STRICTLY for search fields, submit the field by pressing Enter key. For other forms, click on the submit button. CLEAR EXISTING text in an input field before entering new text.
    3. ONLY do what you are asked. Do NOT halluciante additional tasks or actions to perform on the webpage. Eg. if you are asked to open youtube - only open youtube and do not start searching for random things on youtube. 


   """,
    "VERFICATION_AGENT": """Given a conversation and a task, your task is to analyse the conversation and tell if the task is completed. If not, you need to tell what is not completed and suggest next steps to complete the task.""",
    "ENTER_TEXT_AND_CLICK_PROMPT": """This skill enters text into a specified element and clicks another element, both identified by their DOM selector queries.
   Ideal for seamless actions like submitting search queries, this integrated approach ensures superior performance over separate text entry and click commands.
   Successfully completes when both actions are executed without errors, returning True; otherwise, it provides False or an explanatory message of any failure encountered.
   Always prefer this dual-action skill for tasks that combine text input and element clicking to leverage its streamlined operation.""",
    "OPEN_URL_PROMPT": """Opens a specified URL in the web browser instance. Returns url of the new page if successful or appropriate error message if the page could not be opened.""",
    "UPLOAD_FILE_PROMPT": """This skill uploads a file on the page opened by the web browser instance""",
    "GO_BACK_PROMPT": """Goes back to previous page in the browser history. Useful when correcting an incorrect action that led to a new page or when needing to revisit a previous page for information. Returns the full URL of the page after the back action is performed.""",
    "COMMAND_EXECUTION_PROMPT": """Execute the user task "$command" $current_url_prompt_segment""",
    "GET_USER_INPUT_PROMPT": """Get clarification by asking the user or wait for user to perform an action on webpage. This is useful e.g. when you encounter a login or captcha and requires the user to intervene. This skill will also be useful when task is ambigious and you need more clarification from the user (e.g. ["which source website to use to accomplish a task"], ["Enter your credentials on your webpage and type done to continue"]). Use this skill very sparingly and only when absolutely needed.""",
    "GET_DOM_WITHOUT_CONTENT_TYPE_PROMPT": """Retrieves the DOM of the current web browser page.
   Each DOM element will have an \"mmid\" attribute injected for ease of DOM interaction.
   Returns a minified representation of the HTML DOM where each HTML DOM Element has an attribute called \"mmid\" for ease of DOM query selection. When \"mmid\" attribute is available, use it for DOM query selectors.""",
    # This one below had all three content types including input_fields
    "GET_DOM_WITH_CONTENT_TYPE_PROMPT": """Retrieves the DOM of the current web site based on the given content type.
   The DOM representation returned contains items ordered in the same way they appear on the page. Keep this in mind when executing user requests that contain ordinals or numbered items.
   text_only - returns plain text representing all the text in the web site. Use this for any information retrieval task. This will contain the most complete textual information.
   input_fields - returns a JSON string containing a list of objects representing text input html elements with mmid attribute. Use this strictly for interaction purposes with text input fields.
   all_fields - returns a JSON string containing a list of objects representing all interactive elements and their attributes with mmid attribute. Use this strictly to identify and interact with any type of elements on page.
   If information is not available in one content type, you must try another content_type.""",
    "GET_ACCESSIBILITY_TREE": """Retrieves the accessibility tree of the current web site.
   The DOM representation returned contains items ordered in the same way they appear on the page. Keep this in mind when executing user requests that contain ordinals or numbered items.""",
    "CLICK_PROMPT": """Executes a click action on the element matching the given mmid attribute value. It is best to use mmid attribute as the selector.
   Returns Success if click was successful or appropriate error message if the element could not be clicked.""",
    "CLICK_PROMPT_ACCESSIBILITY": """Executes a click action on the element a name and role.
   Returns Success if click was successful or appropriate error message if the element could not be clicked.""",
    "GET_URL_PROMPT": """Get the full URL of the current web page/site. If the user command seems to imply an action that would be suitable for an already open website in their browser, use this to fetch current website URL.""",
    "ENTER_TEXT_PROMPT": """Single enter given text in the DOM element matching the given mmid attribute value. This will only enter the text and not press enter or anything else.
   Returns Success if text entry was successful or appropriate error message if text could not be entered.""",
    "CLICK_BY_TEXT_PROMPT": """Executes a click action on the element matching the text. If multiple text matches are found, it will click on all of them. Use this as last resort when all else fails.""",
    "BULK_ENTER_TEXT_PROMPT": """Bulk enter text in multiple DOM fields. To be used when there are multiple fields to be filled on the same page. Typically use this when you see a form to fill with multiple inputs. Make sure to have mmid from a get DOM tool before hand.
   Enters text in the DOM elements matching the given mmid attribute value.
   The input will receive a list of objects containing the DOM query selector and the text to enter.
   This will only enter the text and not press enter or anything else.
   Returns each selector and the result for attempting to enter text.""",
    "PRESS_KEY_COMBINATION_PROMPT": """Presses the given key on the current web page.
   This is useful for pressing the enter button to submit a search query, PageDown to scroll, ArrowDown to change selection in a focussed list etc.""",
    "ADD_TO_MEMORY_PROMPT": """"Save any information that you may need later in this term memory. This could be useful for saving things to do, saving information for personalisation, or even saving information you may need in future for efficiency purposes E.g. Remember to call John at 5pm, This user likes Tesla company and considered buying shares, The user enrollment form is available in <url> etc.""",
    "HOVER_PROMPT": """Hover on a element with the given mmid attribute value. Hovering on an element can reveal additional information such as a tooltip or trigger a dropdown menu with different navigation options.""",
    "GET_MEMORY_PROMPT": """Retrieve all the information previously stored in the memory""",
    "PRESS_ENTER_KEY_PROMPT": """Presses the enter key in the given html field. This is most useful on text input fields.""",
    "EXTRACT_TEXT_FROM_PDF_PROMPT": """Extracts text from a PDF file hosted at the given URL.""",
    "BROWSER_AGENT_NO_SKILLS_PROMPT": """You are an autonomous agent tasked with performing web navigation on a Playwright instance, including logging into websites and executing other web-based actions.
   You will receive user commands, formulate a plan and then write the PYTHON code that is needed for the task to be completed.
   It is possible that the code you are writing is for one step at a time in the plan. This will ensure proper execution of the task.
   Your operations must be precise and efficient, adhering to the guidelines provided below:
   1. **Asynchronous Code Execution**: Your tasks will often be asynchronous in nature, requiring careful handling. Wrap asynchronous operations within an appropriate async structure to ensure smooth execution.
   2. **Sequential Task Execution**: To avoid issues related to navigation timing, execute your actions in a sequential order. This method ensures that each step is completed before the next one begins, maintaining the integrity of your workflow. Some steps like navigating to a site will require a small amount of wait time after them to ensure they load correctly.
   3. **Error Handling and Debugging**: Implement error handling to manage exceptions gracefully. Should an error occur or if the task doesn't complete as expected, review your code, adjust as necessary, and retry. Use the console or logging for debugging purposes to track the progress and issues.
   4. **Using HTML DOM**: Do not assume what a DOM selector (web elements) might be. Rather, fetch the DOM to look for the selectors or fetch DOM inner text to answer a questions. This is crucial for accurate task execution. When you fetch the DOM, reason about its content to determine appropriate selectors or text that should be extracted. To fetch the DOM using playwright you can:
       - Fetch entire DOM using page.content() method. In the fetched DOM, consider if appropriate to remove entire sections of the DOM like `script`, `link` elements
       - Fetch DOM inner text only text_content = await page.evaluate("() => document.body.innerText || document.documentElement.innerText"). This is useful for information retrieval.
   5. **DOM Handling**: Never ever substring the extracted HTML DOM. You can remove entire sections/elements of the DOM like `script`, `link` elements if they are not needed for the task. This is crucial for accurate task execution.
   6. **Execution Verification**: After executing the user the given code, ensure that you verify the completion of the task. If the task is not completed, revise your plan then rewrite the code for that step.
   7. **Termination Protocol**: Once a task is verified as complete or if it's determined that further attempts are unlikely to succeed, conclude the operation and respond with `##TERMINATE##`, to indicate the end of the session. This signal should only be used when the task is fully completed or if there's a consensus that continuation is futile.
   8. **Code Modification and Retry Strategy**: If your initial code doesn't achieve the desired outcome, revise your approach based on the insights gained during the process. When DOM selectors you are using fail, fetch the DOM and reason about it to discover the right selectors.If there are timeouts, adjust increase times. Add other error handling mechanisms before retrying as needed.
   9. **Code Generation**: Generated code does not need documentation or usage examples. Assume that it is being executed by an autonomous agent acting on behalf of the user. Do not add placeholders in the code.
   10. **Browser Handling**: Do not user headless mode with playwright. Do not close the browser after every step or even after task completion. Leave it open.
   11. **Reponse**: Remember that you are communicating with an autonomous agent that does not reason. All it does is execute code. Only respond with code that it can execute unless you are terminating.
   12. **Playwrite Oddities**: There are certain things that Playwright does not do well:
       - page.wait_for_selector: When providing a timeout value, it will almost always timeout. Put that call in a try/except block and catch the timeout. If timeout occurs just move to the next statement in the code and most likely it will work. For example, if next statement is page.fill, just execute it.


   By following these guidelines, you will enhance the efficiency, reliability, and user interaction of your web navigation tasks.
   Always aim for clear, concise, and well-structured code that aligns with best practices in asynchronous programming and web automation.
   """,
    "JOB_PLANNER_AGENT_PROMPT": """
    You are a web automation task planner specializing in LinkedIn job applications. Your role is to receive job application tasks from the user and work with a naive helper to accomplish them. You will think step by step and break down the tasks into a sequence of simple subtasks, which will be delegated to the helper to execute.

    In the next message - the user will provide you with your task which will be the specific job application related tasks to be completed on LinkedIn.

    You will be provided with one input variable:
    <BASIC_USER_INFORMATION>$basic_user_information</BASIC_USER_INFORMATION>
    This variable contains basic information about the user that may be relevant to the job application process.

    Return Format:
    Your reply will strictly be a well-formatted JSON with four attributes:
    1. "plan": A string containing the high-level plan. This is optional and needs to be present only when a task starts and when the plan needs to be revised. DO NOT ASK USER for anything they want to do extra apart from performing the task. Stick to the task at hand.
    2. "next_step": A string containing a detailed next step that is consistent with the plan. The next step will be delegated to the helper to execute. This needs to be present for every response except when terminating. Once you receive a confirmation from the user that your previous next step HAS BEEN EXECUTED, SEND THE NEXT STEP from the OVERALL plan.
    3. "terminate": yes/no. Return "yes" when the exact task is complete without any compromises or you are absolutely convinced that the task cannot be completed, "no" otherwise. This is mandatory for every response. VERY IMPORTANT - SEND "yes" and TERMINATE as soon as the original task is complete.
    4. "final_response": The final answer string that will be returned to the user. This attribute only needs to be present when terminate is true.

    Capabilities and limitations of the helper:
    1. Helper can navigate to URLs, perform simple interactions on a page, or answer any question you may have about the current page.
    2. Helper cannot perform complex planning, reasoning, or analysis. You will not delegate any such tasks to helper; instead, you will perform them based on information from the helper.
    3. Helper is stateless and treats each step as a new task. Helper will not remember previous pages or actions. So, you will provide all necessary information as part of each step.
    4. Very Important: Helper cannot go back to previous pages. If you need the helper to return to a previous page, you must explicitly add the URL of the previous page in the step.

    Guidelines:
    1. If you know the direct URL, use it directly instead of searching for it (e.g., go to www.linkedin.com/jobs). Optimize the plan to avoid unnecessary steps.
    2. Do not assume any capability exists on the webpage. Ask questions to the helper to confirm the presence of features.
    3. Do not combine multiple steps into one. A step should be strictly as simple as interacting with a single element or navigating to a page.
    4. Important: You will NOT ask for any URLs of hyperlinks in the page from the helper; instead, you will simply ask the helper to click on specific results.
    5. Very Important: Add verification as part of the plan, after each step and specifically before terminating to ensure that the task is completed successfully.
    6. If the task requires multiple pieces of information, all of them are equally important and should be gathered before terminating the task.
    7. If one plan fails, you MUST revise the plan and try a different approach. You will NOT terminate a task until you are absolutely convinced that the task is impossible to accomplish.

    Complexities of web navigation specific to job applications:
    1. Many job application forms have mandatory fields that need to be filled up before they can be submitted. Ask the helper for what fields look mandatory.
    2. LinkedIn often has multiple options to filter or sort job listings. Ask the helper to list any elements on the page which will help narrow down the job search.
    3. Always keep in mind complexities such as filtering, advanced search, sorting, and other features that may be present on LinkedIn. Ask the helper whether these features are available on the page when relevant and use them when the task requires it.
    4. Job listings on LinkedIn are often divided into multiple pages. If you need complete information, it is critical to explicitly ask the helper to go through all the pages.
    5. Sometimes search capabilities available on LinkedIn may not yield the optimal results. Revise the search query to be either more specific or more generic.
    6. When navigating through the application process, information entered in previous pages may be lost. Check that the information needs to be re-entered.
    7. Some elements in the application process may not be visible or be disabled until some other action is performed. Ask the helper to confirm if there are any other fields that may need to be interacted with for elements to appear or be enabled.
    8. ONLY USE LinkedIn Easy Apply - and use as many of default values as you can and just move ahead in the application process.

    CLOSELY MIMIC THE BELOW EXAMPLE IN YOUR LINKEDIN NAVIGATION - 

    Example:
    Task: Apply for a Software Engineer position at Google on LinkedIn in San Francisco. Current page: www.google.com
    {"plan":"1. Go to www.linkedin.com/jobs.
    2. List the interaction options available on LinkedIn jobs page relevant for job search along with their default values.
    3. Set the job title to 'Software Engineer' and PRESS ENTER.
    4. Confirm that you are on the search results page.
    4. Set the company to 'Google' apply the filter.
    5. Clear the existing location set in the location field.
    6. Set the location to 'San Francisco' and press Enter to see all available positions in Google in San Francisco.
    7. Click on the search button to get the search results.
    8. Confirm that you are on the search results page.
    9. Ask the helper to list the available Software Engineer positions at Google.
    10. Select the most relevant position which also has LinkedIn Easy Apply In it.
    11. Click on the 'Easy Apply' button for the selected position.
    12. Fill in the application form with the user's default information. Make up information that you don't have.
    13. Review the application before submitting.
    14. Submit the application.
    15. Confirm that the application has been submitted successfully.",
    "next_step": "Go to https://www.linkedin.com/jobs",
    "terminate":"no"}

    After the task is completed and when terminating:
    Your reply: {"terminate":"yes", "final_response": "Successfully applied for the Software Engineer position at Google on LinkedIn. The application for <specific job title> has been submitted."}

    Remember: You are a very persistent planner who will try every possible strategy to accomplish the job application task perfectly. Revise search queries if needed, ask for more information if needed, and always verify the results before terminating the task.

    Now, proceed with the task of applying for jobs on LinkedIn as specified in the TASK variable. Use the information provided in the BASIC_USER_INFORMATION variable to fill in application details when necessary. Always maintain the JSON format in your responses, and provide detailed, step-by-step instructions to the helper.
""",
    "CUSTOM_WEB_NAVIGATOR_AGENT_PROMPT": """
    You are an AI assistant designed to perform web navigation tasks. Your primary goal is to complete the given task accurately and efficiently while STRICTLY adhering to the instructions provided below:

    1. General Guidelines:
    - Use only the functions made available to you for web interactions.
    - Execute functions sequentially to avoid navigation timing issues.
    - Do not parallelize actions; they are intended for sequential execution.
    - If a task is ambiguous or has multiple options, ask the user for clarification.
    - Do not make assumptions or provide information from memory.
    - NEVER ask the user what to do next or how they would like to proceed.

    2. DOM Representation and Element Interaction:
    - Use the provided DOM representation for element location or text summarization.
    - Interact with page elements using only the "mmid" attribute in DOM elements.
    - Extract mmid values from the fetched DOM; do NOT invent them. do NOT hallucinate.
    - Do not provide any mmid values in your responses.

    3. Function Execution and Task Completion:
    - Call one function at a time and wait for its response before invoking the next.
    - Adjust your approach based on function feedback about action success and observed changes.
    - Once a task is completed or cannot be completed, provide a short summary of your actions.
    - Confirm task completion with ##TERMINATE TASK##.

    4. VERY IMPORTANT Web Interaction Instructions:
    - CLEAR EXISTING VALUES in text/search fields by calling a press key combination tool and then call another tool to enter text/ enter text and click.
    - For search fields, submit by pressing the Enter key.
    - For other forms, click on the submit button.
    - Follow the format of input fields (e.g., date format) when entering information.
    - Perform tasks on the current page unless explicitly instructed to navigate to a new URL.
    - Do not provide URLs of links on webpages; instead, offer to click on them using the link text.

    5. Error Handling and Task Termination:
    - If you encounter issues or are unsure how to proceed, terminate the task and provide a detailed summary of the exact issue. Terminate with ##TERMINATE TASK##
    - Do not repeat the same action multiple times if it fails.
    - If something doesn't work after a few attempts, terminate the task with ##TERMINATE TASK##

    6. Output Formatting and Task Summary:
    - Provide a short and precise answer if the task requires one.
    - Follow your answer with a short summary of actions performed, what worked, and what didn't.
    - Always end your response with ##TERMINATE TASK##.

    7. Important Reminders and Restrictions:
    - Do not use any functions that haven't been provided to you.
    - Do not modify or extend the provided functions.
    - Ensure that user questions are answered from the DOM and not from memory or assumptions.
    - Use text_only DOM type for textual information and all_fields DOM type for interactive elements.

    Begin the task now, following all the guidelines provided above. Remember to terminate the task appropriately when completed or if you encounter any issues.
""",
}

```

# jobber_fsm/core/skills/__init__.py

```py
from jobber_fsm.core.skills.click_using_selector import (
    click,
    do_click,
    is_element_present,
    perform_javascript_click,
    perform_playwright_click,
)
from jobber_fsm.core.skills.enter_text_and_click import enter_text_and_click
from jobber_fsm.core.skills.enter_text_using_selector import (
    bulk_enter_text,
    custom_fill_element,
    do_entertext,
)
from jobber_fsm.core.skills.get_dom_with_content_type import get_dom_with_content_type
from jobber_fsm.core.skills.get_url import geturl
from jobber_fsm.core.skills.get_user_input import get_user_input
from jobber_fsm.core.skills.open_url import openurl
from jobber_fsm.core.skills.press_key_combination import press_key_combination

__all__ = (
    click,
    do_click,
    is_element_present,
    perform_javascript_click,
    perform_playwright_click,
    enter_text_and_click,
    bulk_enter_text,
    custom_fill_element,
    do_entertext,
    get_dom_with_content_type,
    geturl,
    get_user_input,
    openurl,
    press_key_combination,
)

```

# jobber_fsm/core/skills/check_login_status.py

```py
"""
Check if user is logged into a website
"""
from typing import Dict
from typing_extensions import Annotated
from jobber_fsm.core.web_driver.playwright import PlaywrightManager
from jobber_fsm.core.skills.dry_run import maybe_skip_action
from jobber_fsm.utils.logger import logger

@maybe_skip_action
async def check_login_status() -> Annotated[Dict[str, bool], "Login status information"]:
    """
    Check if the user is logged into the current website by looking for common login indicators
    
    Returns:
        Dict with 'is_logged_in' boolean and 'indicators' list of what was found
    """
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
    
    if not page:
        return {"is_logged_in": False, "indicators": ["No page loaded"]}
    
    # Check for common login indicators
    login_indicators = [
        # Logged in indicators
        {"selector": "a[href*='logout']", "type": "logged_in", "name": "logout link"},
        {"selector": "button:has-text('Sign Out')", "type": "logged_in", "name": "sign out button"},
        {"selector": "button:has-text('Log Out')", "type": "logged_in", "name": "log out button"},
        {"selector": "[class*='profile']", "type": "logged_in", "name": "profile element"},
        {"selector": "[class*='account-menu']", "type": "logged_in", "name": "account menu"},
        {"selector": "[aria-label*='account']", "type": "logged_in", "name": "account aria label"},
        
        # Not logged in indicators
        {"selector": "a:has-text('Sign In')", "type": "not_logged_in", "name": "sign in link"},
        {"selector": "a:has-text('Log In')", "type": "not_logged_in", "name": "log in link"},
        {"selector": "button:has-text('Sign In')", "type": "not_logged_in", "name": "sign in button"},
        {"selector": "a:has-text('Create Account')", "type": "not_logged_in", "name": "create account link"},
    ]
    
    found_indicators = []
    is_logged_in = False
    
    for indicator in login_indicators:
        try:
            element = await page.query_selector(indicator["selector"])
            if element:
                found_indicators.append(indicator["name"])
                if indicator["type"] == "logged_in":
                    is_logged_in = True
                    break
        except Exception as e:
            logger.debug(f"Error checking {indicator['name']}: {e}")
    
    return {
        "is_logged_in": is_logged_in,
        "indicators": found_indicators,
        "current_url": page.url
    }
```

# jobber_fsm/core/skills/click_using_selector.py

```py
import asyncio
import inspect
import traceback
import os
from typing import Dict

from playwright.async_api import ElementHandle, Page
from typing_extensions import Annotated

from jobber_fsm.core.web_driver.playwright import PlaywrightManager
from jobber_fsm.utils.dom_helper import get_element_outer_html
from jobber_fsm.utils.dom_mutation_observer import (
    subscribe,  # type: ignore
    unsubscribe,  # type: ignore
)
from jobber_fsm.utils.logger import logger
from jobber_fsm.core.skills.dry_run import maybe_skip_action

@maybe_skip_action
async def click(
    selector: Annotated[
        str,
        "The properly formed query selector string to identify the element for the click action (e.g. [mmid='114']). When \"mmid\" attribute is present, use it for the query selector. mmid will always be a number",
    ],
    wait_before_execution: Annotated[
        float,
        "Optional wait time in seconds before executing the click event logic.",
        float,
    ] = 0.0,
) -> Annotated[str, "A message indicating success or failure of the click."]:
    """
    Executes a click action on the element matching the given query selector string within the currently open web page.
    If there is no page open, it will raise a ValueError. An optional wait time can be specified before executing the click logic. Use this to wait for the page to load especially when the last action caused the DOM/Page to load.

    Parameters:
    - selector: The query selector string to identify the element for the click action.
    - wait_before_execution: Optional wait time in seconds before executing the click event logic. Defaults to 0.0 seconds.

    Returns:
    - Success if the click was successful, Appropriate error message otherwise.
    """
    logger.info(f'Executing ClickElement with "{selector}" as the selector')

    # Check if in dry-run mode
    if os.getenv("DRY_RUN", "").lower() in {"1", "true", "yes"}:
        logger.info(f"[dry-run] would click element with selector: {selector}")
        return f"[dry-run] would click element with selector: {selector}"

    # Initialize PlaywrightManager and get the active browser page
    browser_manager = PlaywrightManager(browser_type="chromium", headless=False)
    page = await browser_manager.get_current_page()

    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    function_name = inspect.currentframe().f_code.co_name  # type: ignore

    await browser_manager.take_screenshots(f"{function_name}_start", page)

    await browser_manager.highlight_element(selector, True)

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    subscribe(detect_dom_changes)
    result = await do_click(page, selector, wait_before_execution)
    await asyncio.sleep(
        0.1
    )  # sleep for 100ms to allow the mutation observer to detect changes
    unsubscribe(detect_dom_changes)
    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"Success: {result['summary_message']}.\n As a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action to click {selector} is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]


async def do_click(
    page: Page, selector: str, wait_before_execution: float
) -> Dict[str, str]:
    """
    Executes the click action on the element with the given selector within the provided page.

    Parameters:
    - page: The Playwright page instance.
    - selector: The query selector string to identify the element for the click action.
    - wait_before_execution: Optional wait time in seconds before executing the click event logic.

    Returns:
    Dict[str,str] - Explanation of the outcome of this operation represented as a dictionary with 'summary_message' and 'detailed_message'.
    """
    # Check if in dry-run mode (additional check for internal function)
    if os.getenv("DRY_RUN", "").lower() in {"1", "true", "yes"}:
        return {
            "summary_message": f"[dry-run] would click {selector}",
            "detailed_message": f"[dry-run] would click element with selector: {selector}"
        }
    
    logger.info(
        f'Executing ClickElement with "{selector}" as the selector. Wait time before execution: {wait_before_execution} seconds.'
    )

    # Wait before execution if specified
    if wait_before_execution > 0:
        await asyncio.sleep(wait_before_execution)

    # Wait for the selector to be present and ensure it's attached and visible. If timeout, try javascript click
    try:
        logger.info(
            f'Executing ClickElement with "{selector}" as the selector. Waiting for the element to be attached and visible.'
        )

        element = await asyncio.wait_for(
            page.wait_for_selector(selector, state="attached", timeout=5000),
            timeout=5.0,
        )
        if element is None:
            raise ValueError(f'Element with selector: "{selector}" not found')

        logger.info(
            f'Element with selector: "{selector}" is attached. scrolling it into view if needed.'
        )
        try:
            await element.scroll_into_view_if_needed(timeout=200)
            logger.info(
                f'Element with selector: "{selector}" is attached and scrolled into view. Waiting for the element to be visible.'
            )
        except Exception:
            # If scrollIntoView fails, just move on, not a big deal
            pass

        try:
            await element.wait_for_element_state("visible", timeout=200)
            logger.info(
                f'Executing ClickElement with "{selector}" as the selector. Element is attached and visible. Clicking the element.'
            )
        except Exception:
            # If the element is not visible, try to click it anyway
            pass

        element_tag_name = await element.evaluate(
            "element => element.tagName.toLowerCase()"
        )
        element_outer_html = await get_element_outer_html(
            element, page, element_tag_name
        )

        if element_tag_name == "option":
            element_value = await element.get_attribute(
                "value"
            )  # get the text that is in the value of the option
            parent_element = await element.evaluate_handle(
                "element => element.parentNode"
            )
            await parent_element.select_option(value=element_value)  # type: ignore

            logger.info(f'Select menu option "{element_value}" selected')

            return {
                "summary_message": f'Select menu option "{element_value}" selected',
                "detailed_message": f'Select menu option "{element_value}" selected. The select element\'s outer HTML is: {element_outer_html}.',
            }

        msg = await perform_javascript_click(page, selector)
        return {
            "summary_message": msg,
            "detailed_message": f"{msg} The clicked element's outer HTML is: {element_outer_html}.",
        }  # type: ignore
    except Exception as e:
        logger.error(f'Unable to click element with selector: "{selector}". Error: {e}')
        traceback.print_exc()
        msg = f'Unable to click element with selector: "{selector}" since the selector is invalid. Proceed by retrieving DOM again.'
        return {"summary_message": msg, "detailed_message": f"{msg}. Error: {e}"}


async def is_element_present(page: Page, selector: str) -> bool:
    """
    Checks if an element is present on the page.

    Parameters:
    - page: The Playwright page instance.
    - selector: The query selector string to identify the element.

    Returns:
    - True if the element is present, False otherwise.
    """
    if os.getenv("DRY_RUN", "").lower() in {"1", "true", "yes"}:
        logger.info(f"[dry-run] would check if element {selector} is present")
        return True  # Assume element is present in dry-run mode
        
    element = await page.query_selector(selector)
    return element is not None


async def perform_playwright_click(element: ElementHandle, selector: str):
    """
    Performs a click action on the element using Playwright's click method.

    Parameters:
    - element: The Playwright ElementHandle instance representing the element to be clicked.
    - selector: The query selector string of the element.

    Returns:
    - None
    """
    if os.getenv("DRY_RUN", "").lower() in {"1", "true", "yes"}:
        logger.info(f"[dry-run] would perform playwright click on {selector}")
        return
        
    logger.info(
        f"Performing first Step: Playwright Click on element with selector: {selector}"
    )
    await element.click(force=False, timeout=200)


async def perform_javascript_click(page: Page, selector: str):
    """
    Performs a click action on the element using JavaScript.

    Parameters:
    - page: The Playwright page instance.
    - selector: The query selector string of the element.

    Returns:
    - None
    """
    if os.getenv("DRY_RUN", "").lower() in {"1", "true", "yes"}:
        logger.info(f"[dry-run] would perform javascript click on {selector}")
        return f"[dry-run] would execute JavaScript Click on element with selector: {selector}"
        
    js_code = """(selector) => {
        let element = document.querySelector(selector);

        if (!element) {
            console.log(`perform_javascript_click: Element with selector ${selector} not found`);
            return `perform_javascript_click: Element with selector ${selector} not found`;
        }

        if (element.tagName.toLowerCase() === "option") {
            let value = element.text;
            let parent = element.parentElement;

            parent.value = element.value; // Directly set the value if possible
            // Trigger change event if necessary
            let event = new Event('change', { bubbles: true });
            parent.dispatchEvent(event);

            console.log("Select menu option", value, "selected");
            return "Select menu option: "+ value+ " selected";
        }
        else {
            console.log("About to click selector", selector);
            // If the element is a link, make it open in the same tab
            if (element.tagName.toLowerCase() === "a") {
                element.target = "_self";
                // #TODO: Consider removing this in the future if it causes issues with intended new tab behavior
                element.removeAttribute('target');
                element.removeAttribute('rel');
            }
            let ariaExpandedBeforeClick = element.getAttribute('aria-expanded');
            element.click();
            let ariaExpandedAfterClick = element.getAttribute('aria-expanded');
            if (ariaExpandedBeforeClick === 'false' && ariaExpandedAfterClick === 'true') {
                return "Executed JavaScript Click on element with selector: "+selector +". Very important: As a consequence a menu has appeared where you may need to make further selection. Very important: Get all_fields DOM to complete the action.";
            }
            return "Executed JavaScript Click on element with selector: "+selector;
        }
    }"""
    try:
        logger.info(f"Executing JavaScript click on element with selector: {selector}")
        result: str = await page.evaluate(js_code, selector)
        logger.debug(f"Executed JavaScript Click on element with selector: {selector}")
        return result
    except Exception as e:
        logger.error(
            f"Error executing JavaScript click on element with selector: {selector}. Error: {e}"
        )
        traceback.print_exc()
        return f"Error executing JavaScript click: {e}"
```

# jobber_fsm/core/skills/dry_run.py

```py
# jobber_fsm/core/skills/dry_run.py
"""
Decorator that turns every Playwright-touching skill into a NO-OP when the
process is launched with  --dry-run  (or the env-var DRY_RUN=1).

Usage
-----
from .dry_run import maybe_skip_action

@maybe_skip_action
async def openurl(...):
    ...

A skipped call just logs the intent and returns None.
"""
import functools, os, logging, inspect, asyncio

_LOG = logging.getLogger("jobber.dryrun")
_DRY = os.getenv("DRY_RUN", "").lower() in {"1", "true", "yes"}

def maybe_skip_action(fn):
    is_async = inspect.iscoroutinefunction(fn)

    @functools.wraps(fn)
    async def _async_wrapper(*args, **kwargs):
        if _DRY:
            _LOG.info("[dry-run] would call  %s%r", fn.__name__, args or kwargs)
            return None
        return await fn(*args, **kwargs)

    @functools.wraps(fn)
    def _sync_wrapper(*args, **kwargs):
        if _DRY:
            _LOG.info("[dry-run] would call  %s%r", fn.__name__, args or kwargs)
            return None
        return fn(*args, **kwargs)

    return _async_wrapper if is_async else _sync_wrapper

```

# jobber_fsm/core/skills/enter_text_and_click.py

```py
import asyncio
import inspect

from typing_extensions import Annotated

from jobber_fsm.core.web_driver.playwright import PlaywrightManager
from jobber_fsm.core.skills.click_using_selector import do_click
from jobber_fsm.core.skills.enter_text_using_selector import do_entertext
from jobber_fsm.core.skills.press_key_combination import do_press_key_combination
from jobber_fsm.utils.logger import logger
from jobber_fsm.core.skills.dry_run import maybe_skip_action

@maybe_skip_action
async def enter_text_and_click(
    text_selector: Annotated[
        str,
        "The properly formatted DOM selector query, for example [mmid='1234'], where the text will be entered. Use mmid attribute. mmid will always be a number",
    ],
    text_to_enter: Annotated[
        str,
        "The text that will be entered into the element specified by text_selector.",
    ],
    click_selector: Annotated[
        str,
        "The properly formatted DOM selector query, for example [mmid='1234'], for the element that will be clicked after text entry. mmid will always be a number",
    ],
    wait_before_click_execution: Annotated[
        float, "Optional wait time in seconds before executing the click.", float
    ],
) -> Annotated[
    str, "A message indicating success or failure of the text entry and click."
]:
    """
    Enters text into an element and then clicks on another element.

    Parameters:
    - text_selector: The selector for the element to enter text into. It should be a properly formatted DOM selector query, for example [mmid='1234'], where the text will be entered. Use the mmid attribute.
    - text_to_enter: The text to enter into the element specified by text_selector.
    - click_selector: The selector for the element to click. It should be a properly formatted DOM selector query, for example [mmid='1234'].
    - wait_before_click_execution: Optional wait time in seconds before executing the click action. Default is 0.0.

    Returns:
    - A message indicating the success or failure of the text entry and click.

    Raises:
    - ValueError: If no active page is found. The OpenURL command opens a new page.

    Example usage:
    \`\`\`
    await enter_text_and_click("[mmid='1234']", "Hello, World!", "[mmid='5678']", wait_before_click_execution=1.5)
    \`\`\`
    """
    logger.info(
        f"Entering text '{text_to_enter}' into element with selector '{text_selector}' and then clicking element with selector '{click_selector}'."
    )

    # Initialize PlaywrightManager and get the active browser page
    browser_manager = PlaywrightManager(browser_type="chromium", headless=False)
    page = await browser_manager.get_current_page()
    if page is None:  # type: ignore
        logger.error("No active page found")
        raise ValueError("No active page found. OpenURL command opens a new page.")

    await browser_manager.highlight_element(text_selector, True)

    function_name = inspect.currentframe().f_code.co_name  # type: ignore
    await browser_manager.take_screenshots(f"{function_name}_start", page)

    text_entry_result = await do_entertext(
        page, text_selector, text_to_enter, use_keyboard_fill=True
    )

    # await browser_manager.notify_user(text_entry_result["summary_message"])
    if not text_entry_result["summary_message"].startswith("Success"):
        await browser_manager.take_screenshots(f"{function_name}_end", page)
        return f"Failed to enter text '{text_to_enter}' into element with selector '{text_selector}'. Check that the selctor is valid."

    result = text_entry_result

    # if the text_selector is the same as the click_selector, press the Enter key instead of clicking
    if text_selector == click_selector:
        do_press_key_combination_result = await do_press_key_combination(
            browser_manager, page, "Enter"
        )
        if do_press_key_combination_result:
            result["detailed_message"] += (
                f' Instead of click, pressed the Enter key successfully on element: "{click_selector}".'
            )
            # await browser_manager.notify_user(
            #     f'Pressed the Enter key successfully on element: "{click_selector}".',
            #     message_type=MessageType.ACTION,
            # )
        else:
            result["detailed_message"] += (
                f' Clicking the same element after entering text in it, is of no value. Tried pressing the Enter key on element "{click_selector}" instead of click and failed.'
            )
            # await browser_manager.notify_user(
            #     'Failed to press the Enter key on element "{click_selector}".',
            #     message_type=MessageType.ACTION,
            # )
    else:
        await browser_manager.highlight_element(click_selector, True)

        do_click_result = await do_click(
            page, click_selector, wait_before_click_execution
        )
        result["detailed_message"] += f' {do_click_result["detailed_message"]}'
        # await browser_manager.notify_user(do_click_result["summary_message"])

    await asyncio.sleep(
        0.1
    )  # sleep for 100ms to allow the mutation observer to detect changes

    await browser_manager.take_screenshots(f"{function_name}_end", page)

    return result["detailed_message"]

```

# jobber_fsm/core/skills/enter_text_using_selector.py

```py
import asyncio
import inspect
import traceback
from dataclasses import dataclass
from typing import (
    Dict,
    List,  # noqa: UP035
)

from playwright.async_api import Page
from typing_extensions import Annotated

from jobber_fsm.core.web_driver.playwright import PlaywrightManager
from jobber_fsm.core.skills.press_key_combination import press_key_combination
from jobber_fsm.utils.dom_helper import get_element_outer_html
from jobber_fsm.utils.dom_mutation_observer import subscribe, unsubscribe
from jobber_fsm.utils.logger import logger
from jobber_fsm.core.skills.dry_run import maybe_skip_action


@dataclass
class EnterTextEntry:
    """
    Represents an entry for text input.

    Attributes:
        query_selector (str): A valid DOM selector query. Use the mmid attribute.
        text (str): The text to enter in the element identified by the query_selector.
    """

    query_selector: str
    text: str

    def __getitem__(self, key: str) -> str:
        if key == "query_selector":
            return self.query_selector
        elif key == "text":
            return self.text
        else:
            raise KeyError(f"{key} is not a valid key")

@maybe_skip_action
async def custom_fill_element(page: Page, selector: str, text_to_enter: str):
    """
    Sets the value of a DOM element to a specified text without triggering keyboard input events.

    This function directly sets the 'value' property of a DOM element identified by the given CSS selector,
    effectively changing its current value to the specified text. This approach bypasses the need for
    simulating keyboard typing, providing a more efficient and reliable way to fill in text fields,
    especially in automated testing scenarios where speed and accuracy are paramount.

    Args:
        page (Page): The Playwright Page object representing the browser tab in which the operation will be performed.
        selector (str): The CSS selector string used to locate the target DOM element. The function will apply the
                        text change to the first element that matches this selector.
        text_to_enter (str): The text value to be set in the target element. Existing content will be overwritten.

    Example:
        await custom_fill_element(page, '#username', 'test_user')

    Note:
        This function does not trigger input-related events (like 'input' or 'change'). If application logic
        relies on these events being fired, additional steps may be needed to simulate them.
    """
    selector = f"{selector}"  # Ensures the selector is treated as a string
    try:
        result = await page.evaluate(
            """(inputParams) => {
            const selector = inputParams.selector;
            let text_to_enter = inputParams.text_to_enter;
            text_to_enter = text_to_enter.trim();
            const element = document.querySelector(selector);
            if (!element) {
                throw new Error(`Element not found: ${selector}`);
            }
            element.value = text_to_enter;
            return `Value set for ${selector}`;
        }""",
            {"selector": selector, "text_to_enter": text_to_enter},
        )
        logger.debug(f"custom_fill_element result: {result}")
    except Exception as e:
        logger.error(f"Error in custom_fill_element: {str(e)}")
        logger.error(f"Selector: {selector}, Text: {text_to_enter}")
        raise


async def entertext(
    entry: Annotated[
        EnterTextEntry,
        "An object containing 'query_selector' (DOM selector query using mmid attribute e.g. [mmid='114']) and 'text' (text to enter on the element). mmid will always be a number",
    ],
) -> Annotated[str, "Explanation of the outcome of this operation."]:
    """
    Enters text into a DOM element identified by a CSS selector.

    This function enters the specified text into a DOM element identified by the given CSS selector.
    It uses the Playwright library to interact with the browser and perform the text entry operation.
    The function supports both direct setting of the 'value' property and simulating keyboard typing.

    Args:
        entry (EnterTextEntry): An object containing 'query_selector' (DOM selector query using mmid attribute)
                                and 'text' (text to enter on the element).

    Returns:
        str: Explanation of the outcome of this operation.

    Example:
        entry = EnterTextEntry(query_selector='#username', text='test_user')
        result = await entertext(entry)

    Note:
        - The 'query_selector' should be a valid CSS selector that uniquely identifies the target element.
        - The 'text' parameter specifies the text to be entered into the element.
        - The function uses the PlaywrightManager to manage the browser instance.
        - If no active page is found, an error message is returned.
        - The function internally calls the 'do_entertext' function to perform the text entry operation.
        - The 'do_entertext' function applies a pulsating border effect to the target element during the operation.
        - The function first clears any existing text in the input field before entering the new text.
        - The 'use_keyboard_fill' parameter in 'do_entertext' determines whether to simulate keyboard typing or not.
        - If 'use_keyboard_fill' is set to True, the function uses the 'page.keyboard.type' method to enter the text.
        - If 'use_keyboard_fill' is set to False, the function uses the 'custom_fill_element' method to enter the text.
    """
    logger.info(f"Entering text: {entry}")

    if isinstance(entry, dict):
        query_selector: str = entry["query_selector"]
        text_to_enter: str = entry["text"]
    elif isinstance(entry, EnterTextEntry):
        query_selector: str = entry.query_selector
        text_to_enter: str = entry.text
    else:
        raise ValueError(
            "Invalid input type for 'entry'. Expected EnterTextEntry or dict."
        )

    if not isinstance(query_selector, str) or not isinstance(text_to_enter, str):
        raise ValueError("query_selector and text must be strings")

    # logger.info(
    #     f"######### Debug: query_selector={query_selector}, text_to_enter={text_to_enter}"
    # )

    # Create and use the PlaywrightManager
    browser_manager = PlaywrightManager(browser_type="chromium", headless=False)
    page = await browser_manager.get_current_page()
    if page is None:  # type: ignore
        return "Error: No active page found. OpenURL command opens a new page."

    function_name = inspect.currentframe().f_code.co_name  # type: ignore

    await browser_manager.take_screenshots(f"{function_name}_start", page)

    await browser_manager.highlight_element(query_selector, True)

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    subscribe(detect_dom_changes)

    # Clear existing text before entering new text
    # await page.evaluate(f"document.querySelector('{query_selector}').value = '';")
    # logger.info(
    #     f"######### About to page.evaluate: selector={query_selector}, text={text_to_enter}"
    # )
    await page.evaluate(
        """
        (selector) => {
            const element = document.querySelector(selector);
            if (element) {
                element.value = '';
            } else {
                console.error('Element not found:', selector);
            }
        }
        """,
        query_selector,
    )
    # logger.info(
    #     f"######### About to call do_entertext with: selector={query_selector}, text={text_to_enter}"
    # )
    result = await do_entertext(page, query_selector, text_to_enter)
    # logger.info(f"#########do_entertext returned: {result}")
    await asyncio.sleep(
        0.1
    )  # sleep for 100ms to allow the mutation observer to detect changes
    unsubscribe(detect_dom_changes)

    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"{result['detailed_message']}.\n As a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action of entering text {text_to_enter} is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]


async def do_entertext(
    page: Page, selector: str, text_to_enter: str, use_keyboard_fill: bool = True
):
    """
    Performs the text entry operation on a DOM element.

    This function performs the text entry operation on a DOM element identified by the given CSS selector.
    It applies a pulsating border effect to the element during the operation for visual feedback.
    The function supports both direct setting of the 'value' property and simulating keyboard typing.

    Args:
        page (Page): The Playwright Page object representing the browser tab in which the operation will be performed.
        selector (str): The CSS selector string used to locate the target DOM element.
        text_to_enter (str): The text value to be set in the target element. Existing content will be overwritten.
        use_keyboard_fill (bool, optional): Determines whether to simulate keyboard typing or not.
                                            Defaults to False.

    Returns:
        Dict[str, str]: Explanation of the outcome of this operation represented as a dictionary with 'summary_message' and 'detailed_message'.

    Example:
        result = await do_entertext(page, '#username', 'test_user')

    Note:
        - The 'use_keyboard_fill' parameter determines whether to simulate keyboard typing or not.
        - If 'use_keyboard_fill' is set to True, the function uses the 'page.keyboard.type' method to enter the text.
        - If 'use_keyboard_fill' is set to False, the function uses the 'custom_fill_element' method to enter the text.
    """
    try:
        elem = await page.query_selector(selector)

        if elem is None:
            error = f"Error: Selector {selector} not found. Unable to continue."
            return {"summary_message": error, "detailed_message": error}

        # logger.info(f"######### Found selector {selector} to enter text")
        element_outer_html = await get_element_outer_html(elem, page)

        if use_keyboard_fill:
            await elem.focus()
            await asyncio.sleep(0.1)
            await press_key_combination("Control+A")
            await asyncio.sleep(0.1)
            await press_key_combination("Backspace")
            await asyncio.sleep(0.1)
            logger.debug(f"Focused element with selector {selector} to enter text")
            # add a 100ms delay
            await page.keyboard.type(text_to_enter, delay=1)
        else:
            await custom_fill_element(page, selector, text_to_enter)
        await elem.focus()
        logger.info(
            f'Success. Text "{text_to_enter}" set successfully in the element with selector {selector}'
        )
        success_msg = f'Success. Text "{text_to_enter}" set successfully in the element with selector {selector}'
        return {
            "summary_message": success_msg,
            "detailed_message": f"{success_msg} and outer HTML: {element_outer_html}.",
        }

    except Exception as e:
        traceback.print_exc()
        error = f"Error entering text in selector {selector}."
        # logger.info("Error in do_entertext", error)
        return {"summary_message": error, "detailed_message": f"{error} Error: {e}"}


async def bulk_enter_text(
    entries: Annotated[
        List[Dict[str, str]],
        "List of objects, each containing 'query_selector' and 'text'.",
    ],  # noqa: UP006
) -> Annotated[
    List[Dict[str, str]],
    "List of dictionaries, each containing 'query_selector' and the result of the operation.",
]:  # noqa: UP006
    """
    Enters text into multiple DOM elements using a bulk operation.

    This function enters text into multiple DOM elements using a bulk operation.
    It takes a list of dictionaries, where each dictionary contains a 'query_selector' and 'text' pair.
    The function internally calls the 'entertext' function to perform the text entry operation for each entry.

    Args:
        entries: List of objects, each containing 'query_selector' and 'text'.

    Returns:
        List of dictionaries, each containing 'query_selector' and the result of the operation.

    Example:
        entries = [
            {"query_selector": "#username", "text": "test_user"},
            {"query_selector": "#password", "text": "test_password"}
        ]
        results = await bulk_enter_text(entries)

    Note:
        - Each entry in the 'entries' list should be a dictionary with 'query_selector' and 'text' keys.
        - The result is a list of dictionaries, where each dictionary contains the 'query_selector' and the result of the operation.
    """

    results: List[Dict[str, str]] = []  # noqa: UP006
    logger.info("Executing bulk Enter Text Command")
    for entry in entries:
        query_selector = entry["query_selector"]
        text_to_enter = entry["text"]
        logger.info(
            f"Entering text: {text_to_enter} in element with selector: {query_selector}"
        )
        result = await entertext(
            EnterTextEntry(query_selector=query_selector, text=text_to_enter)
        )

        results.append({"query_selector": query_selector, "result": result})

    return results

```

# jobber_fsm/core/skills/get_dom_with_content_type.py

```py
import os
import time
from typing import Any, Union, Dict

from playwright.async_api import Page
from typing_extensions import Annotated

from jobber_fsm.config.config import SOURCE_LOG_FOLDER_PATH
from jobber_fsm.core.web_driver.playwright import PlaywrightManager
from jobber_fsm.utils.dom_helper import wait_for_non_loading_dom_state
from jobber_fsm.utils.get_detailed_accessibility_tree import do_get_accessibility_info
from jobber_fsm.utils.logger import logger
from jobber_fsm.core.skills.dry_run import maybe_skip_action

@maybe_skip_action
async def get_dom_with_content_type(
    content_type: Annotated[
        str,
        "The type of content to extract: 'text_only': Extracts the innerText of the highest element in the document and responds with text, or 'input_fields': Extracts the text input and button elements in the dom.",
    ],
) -> Annotated[
    Union[Dict[str, Any], str, None],
    "The output based on the specified content type.",
]:
    """
    Retrieves and processes the DOM of the active page in a browser instance based on the specified content type.

    Parameters
    ----------
    content_type : str
        The type of content to extract. Possible values are:
        - 'text_only': Extracts the innerText of the highest element in the document and responds with text.
        - 'input_fields': Extracts the text input and button elements in the DOM and responds with a JSON object.
        - 'all_fields': Extracts all the fields in the DOM and responds with a JSON object.

    Returns
    -------
    Dict[str, Any] | str | None
        The processed content based on the specified content type. This could be:
        - A JSON object for 'input_fields' with just inputs.
        - Plain text for 'text_only'.
        - A minified DOM represented as a JSON object for 'all_fields'.

    Raises
    ------
    ValueError
        If an unsupported content_type is provided.
    """

    logger.info(f"Executing Get DOM Command based on content_type: {content_type}")
    start_time = time.time()
    # Create and use the PlaywrightManager
    browser_manager = PlaywrightManager(browser_type="chromium", headless=False)
    page = await browser_manager.get_current_page()
    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    extracted_data = None
    await wait_for_non_loading_dom_state(
        page, 2000
    )  # wait for the DOM to be ready, non loading means external resources do not need to be loaded
    user_success_message = ""
    if content_type == "all_fields":
        user_success_message = "Fetched all the fields in the DOM"
        extracted_data = await do_get_accessibility_info(page, only_input_fields=False)
    elif content_type == "input_fields":
        logger.debug("Fetching DOM for input_fields")
        extracted_data = await do_get_accessibility_info(page, only_input_fields=True)
        if extracted_data is None:
            return "Could not fetch input fields. Please consider trying with content_type all_fields."
        user_success_message = "Fetched only input fields in the DOM"
    elif content_type == "text_only":
        # Extract text from the body or the highest-level element
        logger.debug("Fetching DOM for text_only")
        text_content = await get_filtered_text_content(page)
        with open(
            os.path.join(SOURCE_LOG_FOLDER_PATH, "text_only_dom.txt"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(text_content)
        extracted_data = text_content
        user_success_message = "Fetched the text content of the DOM"
    else:
        raise ValueError(f"Unsupported content_type: {content_type}")

    elapsed_time = time.time() - start_time
    logger.info(f"Get DOM Command executed in {elapsed_time} seconds")
    # await browser_manager.notify_user(
    #     user_success_message, message_type=MessageType.ACTION
    # )
    return extracted_data  # type: ignore


async def get_filtered_text_content(page: Page) -> str:
    text_content = await page.evaluate("""
        () => {
            // Array of query selectors to filter out
            const selectorsToFilter = ['#agente-overlay'];

            // Store the original visibility values to revert later
            const originalStyles = [];

            // Hide the elements matching the query selectors
            selectorsToFilter.forEach(selector => {
                const elements = document.querySelectorAll(selector);
                elements.forEach(element => {
                    originalStyles.push({ element: element, originalStyle: element.style.visibility });
                    element.style.visibility = 'hidden';
                });
            });

            // Get the text content of the page
            let textContent = document?.body?.innerText || document?.documentElement?.innerText || "";

            // Get all the alt text from images on the page
            let altTexts = Array.from(document.querySelectorAll('img')).map(img => img.alt);
            altTexts="Other Alt Texts in the page: " + altTexts.join(' ');

            // Revert the visibility changes
            originalStyles.forEach(entry => {
                entry.element.style.visibility = entry.originalStyle;
            });
            textContent=textContent+" "+altTexts;
            return textContent;
        }
    """)
    return text_content

```

# jobber_fsm/core/skills/get_screenshot.py

```py
import base64

from typing_extensions import Annotated

from jobber_fsm.core.web_driver.playwright import PlaywrightManager
from jobber_fsm.utils.logger import logger
from jobber_fsm.core.skills.dry_run import maybe_skip_action

@maybe_skip_action
async def get_screenshot() -> (
    Annotated[
        str, "Returns a base64 encoded screenshot of the current active web page."
    ]
):
    """
    Captures and returns a base64 encoded screenshot of the current page (only the visible viewport and not the full page)

    Returns:
    - Base64 encoded string of the screenshot image.
    """

    try:
        # Create and use the PlaywrightManager
        browser_manager = PlaywrightManager(browser_type="chromium", headless=False)
        page = await browser_manager.get_current_page()
        logger.info("page {page}")

        if not page:
            logger.info("No active page found. OpenURL command opens a new page.")
            raise ValueError("No active page found. OpenURL command opens a new page.")

        await page.wait_for_load_state("domcontentloaded")

        # Capture the screenshot
        logger.info("about to capture")
        screenshot_bytes = await page.screenshot(full_page=False)

        # Encode the screenshot as base64
        base64_screenshot = base64.b64encode(screenshot_bytes).decode("utf-8")

        return f"data:image/png;base64,{base64_screenshot}"

    except Exception as e:
        raise ValueError(
            "Failed to capture screenshot. Make sure a page is open and accessible."
        ) from e

```

# jobber_fsm/core/skills/get_url.py

```py
from typing_extensions import Annotated

from jobber_fsm.core.web_driver.playwright import PlaywrightManager
from jobber_fsm.core.skills.dry_run import maybe_skip_action

@maybe_skip_action
async def geturl() -> (
    Annotated[str, "Returns the full URL of the current active web site/page."]
):
    """
    Returns the full URL of the current page

    Parameters:

    Returns:
    - Full URL the browser's active page.
    """

    try:
        # Create and use the PlaywrightManager
        browser_manager = PlaywrightManager(browser_type="chromium", headless=False)
        page = await browser_manager.get_current_page()

        if not page:
            raise ValueError("No active page found. OpenURL command opens a new page.")

        await page.wait_for_load_state("domcontentloaded")

        # Get the URL of the current page
        try:
            title = await page.title()
            current_url = page.url
            if len(current_url) > 250:
                current_url = current_url[:250] + "..."
            return f"Current Page: {current_url}, Title: {title}"  # type: ignore
        except:  # noqa: E722
            current_url = page.url
            return f"Current Page: {current_url}"

    except Exception as e:
        raise ValueError(
            "No active page found. OpenURL command opens a new page."
        ) from e

```

# jobber_fsm/core/skills/get_user_input.py

```py
from typing import (
    Dict,
    List,  # noqa: UP035,
)

from typing_extensions import Annotated

from jobber_fsm.core.web_driver.playwright import PlaywrightManager
from jobber_fsm.utils.cli_helper import answer_questions_over_cli
from jobber_fsm.core.skills.dry_run import maybe_skip_action

@maybe_skip_action
async def get_user_input(
    questions: Annotated[
        List[str], "List of questions to ask the user each one represented as a string"
    ],
) -> Dict[str, str]:  # noqa: UP006
    """
    Asks the user a list of questions and returns the answers in a dictionary.

    Parameters:
    - questions: A list of questions to ask the user ["What is Username?", "What is your password?"].

    Returns:
    - Newline separated list of questions to ask the user
    """

    answers: Dict[str, str] = {}
    browser_manager = PlaywrightManager(browser_type="chromium", headless=False)
    if browser_manager.ui_manager:
        for question in questions:
            answers[question] = await browser_manager.prompt_user(
                f"Question: {question}"
            )
    else:
        answers = await answer_questions_over_cli(questions)
    return answers

```

# jobber_fsm/core/skills/open_url.py

```py
import inspect

from typing_extensions import Annotated

from jobber_fsm.core.web_driver.playwright import PlaywrightManager
from jobber_fsm.utils.logger import logger
from jobber_fsm.core.skills.dry_run import maybe_skip_action

@maybe_skip_action
async def openurl(
    url: Annotated[
        str,
        "The URL to navigate to. Value must include the protocol (http:// or https://).",
    ],
    timeout: Annotated[int, "Additional wait time in seconds after initial load."],
) -> Annotated[str, "Returns the result of this request in text form"]:
    """
    Opens a specified URL in the active browser instance. Waits for an initial load event, then waits for either
    the 'domcontentloaded' event or a configurable timeout, whichever comes first.

    Parameters:
    - url: The URL to navigate to.
    - timeout: Additional time in seconds to wait after the initial load before considering the navigation successful.

    Returns:
    - URL of the new page.
    """
    logger.info(f"Opening URL: {url}")
    browser_manager = PlaywrightManager(browser_type="chromium", headless=False)
    await browser_manager.get_browser_context()
    page = await browser_manager.get_current_page()
    
    # Navigate to the URL with a short timeout to ensure the initial load starts
    function_name = inspect.currentframe().f_code.co_name  # type: ignore
    try:
        await browser_manager.take_screenshots(f"{function_name}_start", page)
        url = ensure_protocol(url)
        await page.goto(url, timeout=timeout * 1000)  # type: ignore
    except Exception as e:
        logger.warn(
            f"Initial navigation to {url} failed: {e}. Will try to continue anyway."
        )  # happens more often than not, but does not seem to be a problem
        import traceback

        traceback.print_exc()

    await browser_manager.take_screenshots(f"{function_name}_end", page)
    
    # Take debug screenshot if debugger is available
    if hasattr(browser_manager, 'screenshot_debugger') and browser_manager.screenshot_debugger:
        try:
            await browser_manager.screenshot_debugger.capture(
                page, 
                f"opened_url",
                f"Navigated to {url}"
            )
        except Exception as e:
            logger.error(f"Failed to capture screenshot: {e}")


    # Get the page title
    title = await page.title()
    url = page.url
    return f"Page loaded: {url}, Title: {title}"  # type: ignore


def ensure_protocol(url: str) -> str:
    """
    Ensures that a URL has a protocol (http:// or https://). If it doesn't have one,
    https:// is added by default.

    Parameters:
    - url: The URL to check and modify if necessary.

    Returns:
    - A URL string with a protocol.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url  # Default to http if no protocol is specified
        logger.info(
            f"Added 'https://' protocol to URL because it was missing. New URL is: {url}"
        )
    return url
```

# jobber_fsm/core/skills/pdf_text_extractor.py

```py
import os

import httpx
import pdfplumber
from typing_extensions import Annotated

from jobber_fsm.config.config import PROJECT_TEMP_PATH
from jobber_fsm.core.web_driver.playwright import PlaywrightManager
from jobber_fsm.utils.logger import logger
from jobber_fsm.utils.message_type import MessageType
from jobber_fsm.core.skills.dry_run import maybe_skip_action

@maybe_skip_action
async def extract_text_from_pdf(
    pdf_url: Annotated[str, "The URL of the PDF file to extract text from."],
) -> Annotated[str, "All the text found in the PDF file."]:
    """
    Extract text from a PDF file.
    pdf_url: str - The URL of the PDF file to extract text from.
    returns: str - All the text found in the PDF.
    """
    file_path = os.path.join(
        PROJECT_TEMP_PATH, "downloaded_file.pdf"
    )  # fixed file path for downloading the PDF

    try:
        # Create and use the PlaywrightManager
        browser_manager = PlaywrightManager(browser_type="chromium", headless=False)

        # Download the PDF
        download_result = await download_pdf(pdf_url, file_path)
        if not os.path.exists(download_result):
            return download_result  # Return error message if download failed

        # Open the PDF using pdfplumber and extract text
        text = ""
        with pdfplumber.open(download_result) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        extracted_text = text.strip()
        word_count = len(extracted_text.split())
        await browser_manager.notify_user(
            f"Extracted text from the PDF successfully. Found {word_count} words.",
            message_type=MessageType.ACTION,
        )
        return "Text found in the PDF:\n" + extracted_text
    except httpx.HTTPStatusError as e:
        logger.error(
            f"An error occurred while downloading the PDF from {pdf_url}: {str(e)}"
        )
        return f"An error occurred while downloading the PDF: {str(e)}"
    except Exception as e:
        logger.error(
            f"An error occurred while extracting text from the PDF that was downloaded from {pdf_url}: {str(e)}"
        )
        return f"An error occurred while extracting text: {str(e)}"
    finally:
        # Cleanup: Ensure the downloaded file is removed
        cleanup_temp_files(file_path)


def cleanup_temp_files(*file_paths: str) -> None:
    """
    Remove the specified temporary files.

    *file_paths: str - One or more file paths to be removed.
    """
    for file_path in file_paths:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.debug(f"Cleaned file from the filesystem: {file_path}")
            except Exception as e:
                logger.error(f"Failed to remove {file_path}: {str(e)}")
        else:
            logger.debug(
                f"File not found. Unable to clean it from the filesystem: {file_path}"
            )


async def download_pdf(pdf_url: str, file_path: str) -> str:
    """
    Download the PDF file from the given URL and save it to the specified path.

    pdf_url: str - The URL of the PDF file to download.
    file_path: str - The local path to save the downloaded PDF.

    returns: str - The file path of the downloaded PDF if successful, otherwise an error message.
    raises: Exception - If an error occurs during the download process.
    """
    try:
        logger.info(f"Downloading PDF from: {pdf_url} to: {file_path}")
        async with httpx.AsyncClient() as client:
            response = await client.get(pdf_url)
            response.raise_for_status()  # Ensure the request was successful
        with open(file_path, "wb") as pdf_file:
            pdf_file.write(response.content)
        return file_path
    # except httpx.HTTPStatusError as e:
    #     raise e
    except Exception as e:
        raise e

```

# jobber_fsm/core/skills/press_key_combination.py

```py
import asyncio
import inspect

from playwright.async_api import Page  # type: ignore
from typing_extensions import Annotated

from jobber_fsm.core.web_driver.playwright import PlaywrightManager
from jobber_fsm.utils.dom_mutation_observer import (
    subscribe,  # type: ignore
    unsubscribe,  # type: ignore
)
from jobber_fsm.utils.logger import logger
from jobber_fsm.core.skills.dry_run import maybe_skip_action

@maybe_skip_action
async def press_key_combination(
    key_combination: Annotated[str, "The key to press, e.g., Enter, PageDown etc"],
) -> str:
    """
    Presses a key combination on the current active page managed by PlaywrightManager.

    This function simulates the pressing of a key or a combination of keys on the current active web page.
    The `key_combination` should be a string that represents the keys to be pressed, separated by '+' if it's a combination.
    For example, 'Control+C' to copy or 'Alt+F4' to close a window on Windows.

    Parameters:
    - key_combination (Annotated[str, "The key combination to press, e.g., 'Control+C'."]): The key combination to press, represented as a string. For combinations, use '+' as a separator.

    Raises:
    - ValueError: If no active page is found.

    Returns:
    str: status of the operation expressed as a string
    """

    logger.info(f"Executing press_key_combination with key combo: {key_combination}")
    # Create and use the PlaywrightManager
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()

    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    # Split the key combination if it's a combination of keys
    keys = key_combination.split("+")

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    subscribe(detect_dom_changes)
    # If it's a combination, hold down the modifier keys
    for key in keys[:-1]:  # All keys except the last one are considered modifier keys
        await page.keyboard.down(key)

    # Press the last key in the combination
    await page.keyboard.press(keys[-1])

    # Release the modifier keys
    for key in keys[:-1]:
        await page.keyboard.up(key)
    await asyncio.sleep(
        0.1
    )  # sleep for 100ms to allow the mutation observer to detect changes
    unsubscribe(detect_dom_changes)

    if dom_changes_detected:
        return f"Key {key_combination} executed successfully.\n As a consequence of this action, new elements have appeared in view:{dom_changes_detected}. This means that the action is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."

    # await browser_manager.notify_user(
    #     f"Key {key_combination} executed successfully", message_type=MessageType.ACTION
    # )
    return f"Key {key_combination} executed successfully"


async def do_press_key_combination(
    browser_manager: PlaywrightManager, page: Page, key_combination: str
) -> bool:
    """
    Presses a key combination on the provided page.

    This function simulates the pressing of a key or a combination of keys on a web page.
    The `key_combination` should be a string that represents the keys to be pressed, separated by '+' if it's a combination.
    For example, 'Control+C' to copy or 'Alt+F4' to close a window on Windows.

    Parameters:
    - browser_manager (PlaywrightManager): The PlaywrightManager instance.
    - page (Page): The Playwright page instance.
    - key_combination (str): The key combination to press, represented as a string. For combinations, use '+' as a separator.

    Returns:
    bool: True if success and False if failed
    """

    logger.info(f"Executing press_key_combination with key combo: {key_combination}")
    try:
        function_name = inspect.currentframe().f_code.co_name  # type: ignore
        await browser_manager.take_screenshots(f"{function_name}_start", page)
        # Split the key combination if it's a combination of keys
        keys = key_combination.split("+")

        # If it's a combination, hold down the modifier keys
        for key in keys[
            :-1
        ]:  # All keys except the last one are considered modifier keys
            await page.keyboard.down(key)

        # Press the last key in the combination
        await page.keyboard.press(keys[-1])

        # Release the modifier keys
        for key in keys[:-1]:
            await page.keyboard.up(key)

    except Exception as e:
        logger.error(f'Error executing press_key_combination "{key_combination}": {e}')
        return False

    await browser_manager.take_screenshots(f"{function_name}_end", page)

    return True

```

# jobber_fsm/core/skills/upload_file.py

```py
from typing_extensions import Annotated

from jobber_fsm.core.web_driver.playwright import PlaywrightManager
from jobber_fsm.utils.logger import logger
from jobber_fsm.core.skills.dry_run import maybe_skip_action

@maybe_skip_action
async def upload_file(
    # label: Annotated[str, "Label for the element on which upload should happen"],
    selector: Annotated[
        str,
        "The properly formed query selector string to identify the file input element (e.g. [mmid='114']). When \"mmid\" attribute is present, use it for the query selector. mmid will always be a number",
    ],
    file_path: Annotated[str, "Path on the local system for the file to be uploaded"],
) -> Annotated[str, "A meesage indicating if the file uplaod was successful"]:
    """
    Uploads a file.

    Parameters:
    - file_path: Path of the file that needs to be uploaded.

    Returns:
    - A message indicating the success or failure of the file upload
    """
    logger.info(
        f"Uploading file onto the page from {file_path} using selector {selector}"
    )
    print("naman-selector")
    # print(label)
    # label = "Add File"
    browser_manager = PlaywrightManager(browser_type="chromium", headless=False)
    page = await browser_manager.get_current_page()

    if not page:
        raise ValueError("No active page found. OpenURL command opens a new page")

    await page.wait_for_load_state("domcontentloaded")

    try:
        await page.locator(selector).set_input_files(file_path)
        # await page.get_by_label(label).set_input_files(file_path)
        logger.info(
            "File upload was successful. I can confirm it. Please proceed ahead with next step."
        )
    except Exception as e:
        logger.error(f"Failed to upload file: {e}")
        return f"File upload failed {e}"

```

# jobber_fsm/core/skills/wait_for_user.py

```py
"""
Wait for user intervention (e.g., email verification)
"""
import asyncio
from typing_extensions import Annotated
from jobber_fsm.core.skills.dry_run import maybe_skip_action
from jobber_fsm.utils.logger import logger

@maybe_skip_action
async def wait_for_user_action(
    message: Annotated[str, "Message to display to the user"],
    wait_seconds: Annotated[int, "How long to wait in seconds"] = 30
) -> Annotated[str, "Confirmation that wait is complete"]:
    """
    Pause execution and wait for user to complete a manual action
    
    Args:
        message: What action the user needs to take
        wait_seconds: How long to wait
        
    Returns:
        Confirmation message
    """
    logger.info(f"USER ACTION REQUIRED: {message}")
    logger.info(f"Waiting {wait_seconds} seconds for user to complete action...")
    
    # In a real implementation, this could show a UI dialog
    # For now, just wait
    await asyncio.sleep(wait_seconds)
    
    return f"Waited {wait_seconds} seconds for: {message}"
```

# jobber_fsm/core/web_driver/__init__.py

```py

```

# jobber_fsm/core/web_driver/playwright.py

```py
import tempfile
import time
from typing import List, Union

from playwright.async_api import BrowserContext, Page, Playwright
from playwright.async_api import async_playwright as playwright

from jobber_fsm.utils.dom_mutation_observer import (
    dom_mutation_change_detected,
    handle_navigation_for_mutation_observer,
)
from jobber_fsm.utils.logger import logger
from jobber_fsm.utils.ui_messagetype import MessageType

# TODO - Create a wrapper browser manager class that either starts a playwright manager (our solution) or a hosted browser manager like browserbase


class PlaywrightManager:
    _homepage = "https://google.com"
    _playwright = None
    _browser_context = None
    __async_initialize_done = False
    _instance = None
    _take_screenshots = False
    _screenshots_dir = None

    def __new__(cls, *args, **kwargs):  # type: ignore
        """
        Ensures that only one instance of PlaywrightManager is created (singleton pattern).
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.__initialized = False
            logger.debug("Browser instance created..")
        return cls._instance

    def __init__(
        self,
        browser_type: str = "chromium",
        headless: bool = False,
        gui_input_mode: bool = True,
        screenshots_dir: str = "",
        take_screenshots: bool = False,
    ):
        """
        Initializes the PlaywrightManager with the specified browser type and headless mode.
        """
        if self.__initialized:
            return
        
        # Detect cloud environment IMMEDIATELY
        import os
        is_cloud_run = (
            os.getenv('K_SERVICE') is not None or
            os.getenv('K_REVISION') is not None or
            os.getenv('CLOUD_RUN_JOB') is not None or
            os.getenv('GOOGLE_CLOUD_PROJECT') is not None or
            os.path.exists('/.dockerenv')
        )
        
        # Force headless in cloud
        if is_cloud_run:
            headless = True
            logger.info("[PlaywrightManager] Detected cloud environment in __init__ - forcing headless mode")
        
        self.browser_type = browser_type
        self.isheadless = headless
        self.__initialized = True
        self.set_take_screenshots(take_screenshots)
        self.set_screenshots_dir(screenshots_dir)

    async def async_initialize(self, eval_mode: bool = False):
        """
        Asynchronously initialize necessary components and handlers for the browser context.
        """
        if self.__async_initialize_done:
            return

        # Step 1: Ensure Playwright is started and browser context is created
        await self.start_playwright()
        self.eval_mode = eval_mode
        await self.ensure_browser_context()

        # Step 2: Deferred setup of handlers
        # await self.setup_handlers()

        # Step 3: Navigate to homepage
        await self.go_to_homepage()

        self.__async_initialize_done = True

    async def ensure_browser_context(self):
        """
        Ensure that a browser context exists, creating it if necessary.
        """
        if self._browser_context is None:
            await self.create_browser_context()

    # async def setup_handlers(self):
    #     """
    #     Setup various handlers after the browser context has been ensured.
    #     """
    #     await self.set_overlay_state_handler()
    #     await self.set_user_response_handler()
    #     await self.set_navigation_handler()

    async def start_playwright(self):
        """
        Starts the Playwright instance if it hasn't been started yet. This method is idempotent.
        """
        if not PlaywrightManager._playwright:
            PlaywrightManager._playwright: Playwright = await playwright().start()

    async def stop_playwright(self):
        """
        Stops the Playwright instance and resets it to None. This method should be called to clean up resources.
        """
        # Close the browser context if it's initialized
        if PlaywrightManager._browser_context is not None:
            await PlaywrightManager._browser_context.close()
            PlaywrightManager._browser_context = None

        # Stop the Playwright instance if it's initialized
        if PlaywrightManager._playwright is not None:  # type: ignore
            await PlaywrightManager._playwright.stop()
            PlaywrightManager._playwright = None  # type: ignore

    async def create_browser_context(self):
        import os
        
        # Detect if running in Cloud Run/cloud environment
        is_cloud_run = (
            os.getenv('K_SERVICE') is not None or
            os.getenv('K_REVISION') is not None or
            os.getenv('CLOUD_RUN_JOB') is not None or
            os.getenv('GOOGLE_CLOUD_PROJECT') is not None or
            os.path.exists('/.dockerenv')
        )
        
        # Override: always use headless in cloud/docker
        if is_cloud_run:
            self.isheadless = True
            logger.info("[PlaywrightManager] Detected cloud/container environment - forcing headless mode")
        
        try:
            # in eval mode - start a temp browser.
            if self.eval_mode:
                print("Starting in eval mode", self.eval_mode)
                new_user_dir = tempfile.mkdtemp()
                logger.info(
                    f"Starting a temporary browser instance. trying to launch with a new user dir {new_user_dir}"
                )
                PlaywrightManager._browser_context = await PlaywrightManager._playwright.chromium.launch_persistent_context(
                    new_user_dir,
                    channel="chrome",
                    headless=self.isheadless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-session-crashed-bubble",
                        "--disable-infobars",
                    ],
                    no_viewport=True,
                )
            else:
                # Skip Chrome Canary connection attempt in cloud/docker
                if not is_cloud_run:
                    try:
                        # Attempt to reuse a locally-running Chrome started with
                        # --remote-debugging-port=9223 (for Chrome Canary)
                        print("[PlaywrightManager] Attempting to connect to Chrome Canary on port 9223...")
                        browser = await PlaywrightManager._playwright.chromium.connect_over_cdp(
                            "http://localhost:9223", timeout=5_000
                        )
                        PlaywrightManager._browser_context = browser.contexts[0]
                        print(f"[PlaywrightManager] Connected! Found {len(browser.contexts)} contexts")
                        
                        # Navigate to current page to verify connection
                        pages = PlaywrightManager._browser_context.pages
                        if pages:
                            print(f"[PlaywrightManager] Current page URL: {pages[0].url}")
                        else:
                            print("[PlaywrightManager] No pages found, will create one")
                        return  # Exit early if connection successful
                            
                    except Exception as e:
                        print(f"[PlaywrightManager] Failed to connect to Chrome on 9223: {e}")
                
                # Launch browser instance (always for Cloud Run, fallback for local)
                logger.info(f"[PlaywrightManager] About to launch browser...")
                logger.info(f"  Cloud/Docker: {is_cloud_run}")
                logger.info(f"  Headless: {self.isheadless}")
                playwright_version = "Unknown"
                if PlaywrightManager._playwright:
                    try:
                        # Different ways to try to get the version
                        if hasattr(PlaywrightManager._playwright, '__version__'):
                            playwright_version = PlaywrightManager._playwright.__version__
                        elif hasattr(PlaywrightManager._playwright, '_impl_obj'):
                            impl = PlaywrightManager._playwright._impl_obj
                            if hasattr(impl, '_playwright_version'):
                                playwright_version = impl._playwright_version
                    except:
                        pass
                logger.info(f"  Playwright version: {playwright_version}")

                
                # Check if chrome binary exists
                import subprocess
                try:
                    # Try to find chrome executable
                    result = subprocess.run(['which', 'chromium'], capture_output=True, text=True)
                    logger.info(f"  which chromium: {result.stdout.strip() if result.returncode == 0 else 'Not found'}")
                    
                    # Check playwright browsers
                    result = subprocess.run(['playwright', 'show-browsers'], capture_output=True, text=True)
                    logger.info(f"  Playwright browsers: {result.stdout}")
                except Exception as e:
                    logger.error(f"  Error checking browsers: {e}")
                
                # Use chromium channel in cloud, chrome locally
                channel = None if is_cloud_run else "chrome"
                
                logger.info(f"[PlaywrightManager] Launching with channel={channel}")
                
                try:
                    browser = await PlaywrightManager._playwright.chromium.launch(
                        headless=self.isheadless,
                        channel=channel,
                        args=[
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                            "--disable-gpu",
                            "--disable-web-security",
                            "--disable-features=IsolateOrigins,site-per-process",
                            "--disable-blink-features=AutomationControlled",
                            "--single-process",
                            "--disable-setuid-sandbox"
                        ],
                    )
                    logger.info("[PlaywrightManager] Browser launched successfully")
                    
                    PlaywrightManager._browser_context = await browser.new_context(
                        no_viewport=True,
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    )
                    logger.info("[PlaywrightManager] Browser context created successfully")
                    
                except Exception as e:
                    logger.error(f"[PlaywrightManager] Failed to launch browser: {e}")
                    logger.error(f"[PlaywrightManager] Error type: {type(e).__name__}")
                    logger.error(f"[PlaywrightManager] Full error: {str(e)}")
                    import traceback
                    logger.error(f"[PlaywrightManager] Traceback:\n{traceback.format_exc()}")
                    raise

            # Additional step to modify the navigator.webdriver property
            pages = PlaywrightManager._browser_context.pages
            for page in pages:
                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    })
                """)
            
            logger.info("[PlaywrightManager] Browser setup completed successfully")

        except Exception as e:
            logger.error(f"[PlaywrightManager] Browser launch error: {str(e)}")
            logger.error(f"[PlaywrightManager] Error details: {type(e).__name__}")
            
            if "Target page, context or browser has been closed" in str(e):
                new_user_dir = tempfile.mkdtemp()
                logger.error(
                    f"Failed to launch persistent context with provided user data dir: {e} Trying to launch with a new user dir {new_user_dir}"
                )
                
                PlaywrightManager._browser_context = await PlaywrightManager._playwright.chromium.launch_persistent_context(
                    new_user_dir,
                    channel=None if is_cloud_run else "chrome",
                    headless=self.isheadless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-session-crashed-bubble",
                        "--disable-infobars",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--single-process",
                        "--disable-setuid-sandbox"
                    ],
                    no_viewport=True,
                )
            elif "Chromium distribution 'chrome' is not found " in str(e):
                raise ValueError(
                    "Chrome is not installed on this device. Install Google Chrome or install playwright using 'playwright install chrome'. Refer to the readme for more information."
                ) from None
            else:
                raise e from None

    async def get_browser_context(self):
        """
        Returns the existing browser context, or creates a new one if it doesn't exist.
        """
        await self.ensure_browser_context()
        return self._browser_context

    async def get_current_url(self) -> Union[str, None]:
        """
        Get the current URL of current page

        Returns:
            str | None: The current URL if any.
        """
        try:
            current_page: Page = await self.get_current_page()
            return current_page.url
        except Exception:
            pass
        return None

    async def get_current_page(self) -> Page:
        """
        Get the current page of the browser

        Returns:
            Page: The current page if any.
        """
        try:
            browser: BrowserContext = await self.get_browser_context()  # type: ignore
            # Filter out closed pages
            pages: List[Page] = [page for page in browser.pages if not page.is_closed()]
            page: Union[Page, None] = pages[-1] if pages else None
            logger.debug(f"Current page: {page.url if page else None}")
            if page is not None:
                return page
            else:
                page: Page = await browser.new_page()  # type: ignore
                # await stealth_async(page)  # Apply stealth to the new page
                return page
        except Exception as e:
            logger.warn(f"Browser context was closed. Creating a new one. {e}")
        except Exception as e:
            logger.warn(f"Browser context was closed. Creating a new one. {e}")
            PlaywrightManager._browser_context = None
            _browser: BrowserContext = await self.get_browser_context()  # type: ignore
            page: Union[Page, None] = await self.get_current_page()
            return page

    async def close_all_tabs(self, keep_first_tab: bool = True):
        """
        Closes all tabs in the browser context, except for the first tab if `keep_first_tab` is set to True.

        Args:
            keep_first_tab (bool, optional): Whether to keep the first tab open. Defaults to True.
        """
        browser_context = await self.get_browser_context()
        pages: List[Page] = browser_context.pages  # type: ignore
        pages_to_close: List[Page] = pages[1:] if keep_first_tab else pages  # type: ignore
        for page in pages_to_close:  # type: ignore
            await page.close()  # type: ignore

    async def close_except_specified_tab(self, page_to_keep: Page):
        """
        Closes all tabs in the browser context, except for the specified tab.

        Args:
            page_to_keep (Page): The Playwright page object representing the tab that should remain open.
        """
        browser_context = await self.get_browser_context()
        for page in browser_context.pages:  # type: ignore
            if page != page_to_keep:  # Check if the current page is not the one to keep
                await page.close()  # type: ignore

    async def go_to_homepage(self):
        page: Page = await PlaywrightManager.get_current_page(self)
        try:
            await page.goto(self._homepage, timeout=10000)  # 10 seconds timeout
        except Exception as e:
            logger.error(f"Failed to navigate to homepage: {e}")
            # implement a retry mechanism here
        try:
            await page.goto(self._homepage, timeout=10000)  # 10 seconds timeout
        except Exception as e:
            logger.error(f"Failed to navigate to homepage: {e}")
            # implement a retry mechanism here

    async def set_navigation_handler(self):
        page: Page = await PlaywrightManager.get_current_page(self)
        page.on("domcontentloaded", self.ui_manager.handle_navigation)  # type: ignore
        page.on("domcontentloaded", handle_navigation_for_mutation_observer)  # type: ignore
        await page.expose_function(
            "dom_mutation_change_detected", dom_mutation_change_detected
        )  # type: ignore

    async def set_overlay_state_handler(self):
        logger.debug("Setting overlay state handler")
        context = await self.get_browser_context()
        await context.expose_function(
            "overlay_state_changed", self.overlay_state_handler
        )  # type: ignore
        await context.expose_function(
            "show_steps_state_changed", self.show_steps_state_handler
        )  # type: ignore

    async def overlay_state_handler(self, is_collapsed: bool):
        page = await self.get_current_page()
        self.ui_manager.update_overlay_state(is_collapsed)
        if not is_collapsed:
            await self.ui_manager.update_overlay_chat_history(page)

    async def show_steps_state_handler(self, show_details: bool):
        page = await self.get_current_page()
        await self.ui_manager.update_overlay_show_details(show_details, page)

    async def set_user_response_handler(self):
        context = await self.get_browser_context()
        await context.expose_function("user_response", self.receive_user_response)  # type: ignore

    # async def notify_user(
    #     self, message: str, message_type: MessageType = MessageType.STEP
    # ):
    #     """
    #     Notify the user with a message.

    #     Args:
    #         message (str): The message to notify the user with.
    #         message_type (enum, optional): Values can be 'PLAN', 'QUESTION', 'ANSWER', 'INFO', 'STEP'. Defaults to 'STEP'.
    #         To Do: Convert to Enum.
    #     """

    #     if message.startswith(":"):
    #         message = message[1:]

    #     if message.endswith(","):
    #         message = message[:-1]

    #     if message_type == MessageType.PLAN:
    #         message = beautify_plan_message(message)
    #         message = "Plan:\n" + message
    #     elif message_type == MessageType.STEP:
    #         if "confirm" in message.lower():
    #             message = "Verify: " + message
    #         else:
    #             message = "Next step: " + message
    #     elif message_type == MessageType.QUESTION:
    #         message = "Question: " + message
    #     elif message_type == MessageType.ANSWER:
    #         message = "Response: " + message

    #     safe_message = escape_js_message(message)
    #     self.ui_manager.new_system_message(safe_message, message_type)

    #     if self.ui_manager.overlay_show_details == False:  # noqa: E712
    #         if message_type not in (
    #             MessageType.PLAN,
    #             MessageType.QUESTION,
    #             MessageType.ANSWER,
    #             MessageType.INFO,
    #         ):
    #             return

    #     if self.ui_manager.overlay_show_details == True:  # noqa: E712
    #         if message_type not in (
    #             MessageType.PLAN,
    #             MessageType.QUESTION,
    #             MessageType.ANSWER,
    #             MessageType.INFO,
    #             MessageType.STEP,
    #         ):
    #             return

    #     safe_message_type = escape_js_message(message_type.value)
    #     try:
    #         js_code = f"addSystemMessage({safe_message}, is_awaiting_user_response=false, message_type={safe_message_type});"
    #         page = await self.get_current_page()
    #         await page.evaluate(js_code)
    #     except Exception as e:
    #         logger.error(
    #             f'Failed to notify user with message "{message}". However, most likey this will work itself out after the page loads: {e}'
    #         )

    #     self.notification_manager.notify(message, message_type.value)

    async def highlight_element(self, selector: str, add_highlight: bool):
        try:
            page: Page = await self.get_current_page()
            if add_highlight:
                # Add the 'agente-ui-automation-highlight' class to the element. This class is used to apply the fading border.
                await page.eval_on_selector(
                    selector,
                    """e => {
                            let originalBorderStyle = e.style.border;
                            e.classList.add('agente-ui-automation-highlight');
                            e.addEventListener('animationend', () => {
                                e.classList.remove('agente-ui-automation-highlight')
                            });}""",
                )
                logger.debug(
                    f"Applied pulsating border to element with selector {selector} to indicate text entry operation"
                )
            else:
                # Remove the 'agente-ui-automation-highlight' class from the element.
                await page.eval_on_selector(
                    selector,
                    "e => e.classList.remove('agente-ui-automation-highlight')",
                )
                logger.debug(
                    f"Removed pulsating border from element with selector {selector} after text entry operation"
                )
        except Exception:
            # This is not significant enough to fail the operation
            pass

    # async def receive_user_response(self, response: str):
    #     self.user_response = response  # Store the response for later use.
    #     logger.debug(f"Received user response to system prompt: {response}")
    #     # Notify event loop that the user's response has been received.
    #     self.user_response_event.set()

    # async def prompt_user(self, message: str) -> str:
    #     """
    #     Prompt the user with a message and wait for a response.

    #     Args:
    #         message (str): The message to prompt the user with.

    #     Returns:
    #         str: The user's response.
    #     """
    #     logger.debug(f'Prompting user with message: "{message}"')
    #     # self.ui_manager.new_system_message(message)

    #     page = await self.get_current_page()

    #     await self.ui_manager.show_overlay(page)
    #     self.log_system_message(
    #         message, MessageType.QUESTION
    #     )  # add the message to history after the overlay is opened to avoid double adding it. add_system_message below will add it

    #     safe_message = escape_js_message(message)

    #     js_code = f"addSystemMessage({safe_message}, is_awaiting_user_response=true, message_type='question');"
    #     await page.evaluate(js_code)

    #     await self.user_response_event.wait()
    #     result = self.user_response
    #     logger.info(f'User prompt reponse to "{message}": {result}')
    #     self.user_response_event.clear()
    #     self.user_response = ""
    #     self.ui_manager.new_user_message(result)
    #     return result

    def set_take_screenshots(self, take_screenshots: bool):
        self._take_screenshots = take_screenshots

    def get_take_screenshots(self):
        return self._take_screenshots

    def set_screenshots_dir(self, screenshots_dir: str):
        self._screenshots_dir = screenshots_dir

    def get_screenshots_dir(self):
        return self._screenshots_dir

    async def take_screenshots(
        self,
        name: str,
        page: Union[Page, None],
        full_page: bool = True,
        include_timestamp: bool = True,
        load_state: str = "domcontentloaded",
        take_snapshot_timeout: int = 5 * 1000,
    ):
        if not self._take_screenshots:
            return
        if page is None:
            page = await self.get_current_page()

        screenshot_name = name

        if include_timestamp:
            screenshot_name = f"{int(time.time_ns())}_{screenshot_name}"
        screenshot_name += ".png"
        screenshot_path = f"{self.get_screenshots_dir()}/{screenshot_name}"
        try:
            await page.wait_for_load_state(
                state=load_state, timeout=take_snapshot_timeout
            )  # type: ignore
            await page.screenshot(
                path=screenshot_path,
                full_page=full_page,
                timeout=take_snapshot_timeout,
                caret="initial",
                scale="device",
            )
            logger.debug(f"Screen shot saved to: {screenshot_path}")
        except Exception as e:
            logger.error(
                f'Failed to take screenshot and save to "{screenshot_path}". Error: {e}'
            )

    def log_user_message(self, message: str):
        """
        Log the user's message.

        Args:
            message (str): The user's message to log.
        """
        self.ui_manager.new_user_message(message)

    def log_system_message(self, message: str, type: MessageType = MessageType.STEP):
        """
        Log a system message.

        Args:
            message (str): The system message to log.
        """
        self.ui_manager.new_system_message(message, type)

    async def update_processing_state(self, processing_state: str):
        """
        Update the processing state of the overlay.

        Args:
            is_processing (str): "init", "processing", "done"
        """
        page = await self.get_current_page()

        await self.ui_manager.update_processing_state(processing_state, page)

    async def command_completed(
        self, command: str, elapsed_time: Union[float, None] = None
    ):
        """
        Notify the overlay that the command has been completed.
        """
        logger.debug(
            f'Command "{command}" has been completed. Focusing on the overlay input if it is open.'
        )
        page = await self.get_current_page()
        await self.ui_manager.command_completed(page, command, elapsed_time)

```

# jobber_fsm/debug_runner.py

```py
#!/usr/bin/env python3
import sys
import os

print(f"[DEBUG] Python version: {sys.version}", flush=True)
print(f"[DEBUG] Python executable: {sys.executable}", flush=True)
print(f"[DEBUG] Current directory: {os.getcwd()}", flush=True)
print(f"[DEBUG] Directory contents: {os.listdir('.')}", flush=True)
print(f"[DEBUG] Environment PATH: {os.environ.get('PATH', 'Not set')}", flush=True)

# Check if playwright is installed
try:
    import playwright
    print(f"[DEBUG] Playwright imported successfully", flush=True)
    # Try to get version from _repo_version.py
    try:
        from playwright._repo_version import version
        print(f"[DEBUG] Playwright version: {version}", flush=True)
    except:
        print(f"[DEBUG] Playwright version: Unable to determine", flush=True)
except ImportError as e:
    print(f"[DEBUG] Playwright import failed: {e}", flush=True)

# Check playwright browsers
import subprocess
try:
    result = subprocess.run(['playwright', '--version'], capture_output=True, text=True)
    print(f"[DEBUG] Playwright CLI version: {result.stdout.strip()}", flush=True)
    
    result = subprocess.run(['playwright', 'show-browsers'], capture_output=True, text=True)
    print(f"[DEBUG] Playwright browsers installed: {result.stdout}", flush=True)
    if result.stderr:
        print(f"[DEBUG] Playwright browsers stderr: {result.stderr}", flush=True)
except Exception as e:
    print(f"[DEBUG] Failed to check playwright browsers: {e}", flush=True)

# Check if chromium exists
try:
    result = subprocess.run(['find', '/ms-playwright', '-name', 'chrome', '-type', 'f'], capture_output=True, text=True)
    print(f"[DEBUG] Chrome binaries found: {result.stdout}", flush=True)
except Exception as e:
    print(f"[DEBUG] Failed to find chrome binaries: {e}", flush=True)

print("[DEBUG] About to import cloud_job_runner...", flush=True)

try:
    from jobber_fsm import cloud_job_runner
    print("[DEBUG] Successfully imported cloud_job_runner", flush=True)
except Exception as e:
    print(f"[DEBUG] Failed to import cloud_job_runner: {e}", flush=True)
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("[DEBUG] Starting cloud_job_runner.main()...", flush=True)

# Run the actual job
import asyncio
asyncio.run(cloud_job_runner.main())
```

# jobber_fsm/runner.py

```py
"""
Un-attended application runner for Jobber-FSM.

Example:
    poetry run jobber-apply \\
        --url https://boards.greenhouse.io/openai/jobs/1234567 \\
        --profile ./profile.json \\
        --headless --dry-run
"""
from __future__ import annotations
import argparse, asyncio, json, pathlib, sys, os

def _cli() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--url",     required=True, help="Job apply link")
    p.add_argument("--profile", required=True, help="Path to JSON profile file")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--dry-run",  action="store_true")
    return p.parse_args()

# Parse arguments FIRST, before any imports
_args = _cli()

# Set dry-run environment variable BEFORE importing any jobber modules
if _args.dry_run:
    os.environ["DRY_RUN"] = "1"
    print("[runner] DRY-RUN MODE ENABLED (set before imports)")

# NOW import the jobber modules
from jobber_fsm.core.agent.browser_nav_agent import BrowserNavAgent
from jobber_fsm.core.agent.planner_agent     import PlannerAgent
from jobber_fsm.core.models.models           import State
from jobber_fsm.core.orchestrator.orchestrator import Orchestrator
from jobber_fsm.core.memory import ltm
from jobber_fsm.core.models.models import PlannerInput
from jobber_fsm.utils.logger import logger, set_log_level


async def _main() -> None:
    print("[runner] Starting Jobber-FSM...")  # Direct print for immediate feedback
    
    # We already have args from above
    args = _args
    
    # Set up logging first
    log_level = os.getenv("LOGLEVEL", "INFO")
    print(f"[runner] Setting log level to: {log_level}")
    set_log_level(log_level)
    
    # Don't set DRY_RUN again - it's already set above
    if args.dry_run:
        logger.info("[runner] DRY-RUN MODE: Actions will be logged but not executed")

    # ---------- load profile ----------
    print(f"[runner] Loading profile from: {args.profile}")
    profile_path = pathlib.Path(args.profile).expanduser()
    if not profile_path.is_file():
        sys.exit(f"[runner] profile file not found: {profile_path}")
    
    try:
        with profile_path.open() as f:
            profile = json.load(f)
        print(f"[runner] Profile loaded successfully")
        logger.debug(f"[runner] Profile data: {json.dumps(profile, indent=2)}")
    except Exception as e:
        sys.exit(f"[runner] Failed to load profile: {e}")

    # ---------- stash data in memory for agents ----------
    print(f"[runner] Setting up job context for URL: {args.url}")
    ltm.set_job_apply_context(url=args.url, profile=profile)

    # ---------- build agents ----------
    print("[runner] Creating agents...")
    try:
        planner  = PlannerAgent(auto_mode=True)
        executor = BrowserNavAgent(planner, auto_mode=True)
        print("[runner] Agents created successfully")
    except Exception as e:
        print(f"[runner] Failed to create agents: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    state_map = {
        State.PLAN:   planner,
        State.BROWSE: executor,
    }

    print("[runner] Creating orchestrator...")
    orch = Orchestrator(state_to_agent_map=state_map)

    try:
        # 1) spin-up Playwright, but **don't** enter the interactive prompt
        print("[runner] Bootstrapping browser context...")
        await orch._bootstrap()
        print("[runner] Browser context ready")

        # 2) kick off the plan automatically
        print(f"[runner] Starting job application process for: {args.url}")
        logger.info(f"[runner] Objective: Apply via {args.url}")
        
        print("[runner] Creating PlannerInput...")
        planner_input = PlannerInput(
            objective=f"Apply via {args.url}",
            plan=None,
            completed_tasks=None,
            task_for_review=None,
        )
        print(f"[runner] PlannerInput created: {planner_input.model_dump_json()[:100]}...")
        
        print("[runner] Calling planner.process_query()...")
        result = await planner.process_query(planner_input)
        
        print(f"[runner] Planner returned result:")
        print(f"  - Type: {type(result)}")
        print(f"  - is_complete: {result.is_complete if hasattr(result, 'is_complete') else 'N/A'}")
        print(f"  - plan: {result.plan if hasattr(result, 'plan') else 'N/A'}")
        print(f"  - final_response: {result.final_response if hasattr(result, 'final_response') else 'N/A'}")
        
        print("[runner] Job application process completed")
        
    except KeyboardInterrupt:
        print("\n[runner] Process interrupted by user")
    except Exception as e:
        print(f"[runner] Error occurred: {type(e).__name__}: {e}")
        logger.error(f"[runner] Full error details:", exc_info=True)
        import traceback
        traceback.print_exc()
        raise
    finally:
        # 3) optional tidy-up
        if not args.headless:
            print("[runner] Browser kept open for inspection")
        else:
            print("[runner] Cleaning up browser context...")
            # await orch.playwright_manager.stop_playwright()


def entrypoint() -> None:
    """Console script entry point."""
    try:
        asyncio.run(_main())
    except Exception as e:
        print(f"[runner] Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_main())
```

# jobber_fsm/user_preferences/user_preferences.txt

```txt
Personal Info:
First Name: Nischal
Last Name: Jain
Date of birth: 
Pronouns: He/Him

Email: 
Expeted Salary: 5000000
Occupation: Data Engineer
Address: Indiranagr, Bengaluru
Phone Number: 
LinkedIn - https://linkedin.com/in/nischalj10


Here are some of my preferences:
Shopping Preferences: www.amazon.com
Favorite news source: www.thehindu.com
Favorite flight booking site to use with every flight related query: https://www.google.com/travel/flightsno
Resume File to Upload Path = /Users/namanjain/Downloads/DummyResume.pdf
My best project - checkout headless-ollama on google


I am comfortable commuting to office.
I have 4 years of experience in TypeScript, Node Js, Nextjs.
I have work authorization for working in the USA.
I have experience in cloud infrastructure, data warheouses.

```

# jobber_fsm/utils/__init__.py

```py

```

# jobber_fsm/utils/_pydantic.py

```py
from typing import Any, Dict, Tuple, Union, get_args

from pydantic import BaseModel
from pydantic.version import VERSION as PYDANTIC_VERSION
from typing_extensions import get_origin

__all__ = (
    "JsonSchemaValue",
    "model_dump",
    "model_dump_json",
    "type2schema",
    "evaluate_forwardref",
)

PYDANTIC_V1 = PYDANTIC_VERSION.startswith("1.")

if not PYDANTIC_V1:
    from pydantic import TypeAdapter
    from pydantic._internal._typing_extra import (
        eval_type_lenient as evaluate_forwardref,
    )
    from pydantic.json_schema import JsonSchemaValue

    def type2schema(t: Any) -> JsonSchemaValue:
        """Convert a type to a JSON schema

        Args:
            t (Type): The type to convert

        Returns:
            JsonSchemaValue: The JSON schema
        """
        return TypeAdapter(t).json_schema()

    def model_dump(model: BaseModel) -> Dict[str, Any]:
        """Convert a pydantic model to a dict

        Args:
            model (BaseModel): The model to convert

        Returns:
            Dict[str, Any]: The dict representation of the model

        """
        return model.model_dump()

    def model_dump_json(model: BaseModel) -> str:
        """Convert a pydantic model to a JSON string

        Args:
            model (BaseModel): The model to convert

        Returns:
            str: The JSON string representation of the model
        """
        return model.model_dump_json()


# Remove this once we drop support for pydantic 1.x
else:  # pragma: no cover
    from pydantic import TypeAdapter
    from pydantic.typing import (
        evaluate_forwardref as evaluate_forwardref,  # type: ignore[no-redef]
    )

    JsonSchemaValue = Dict[str, Any]  # type: ignore[misc]

    def type2schema(t: Any) -> JsonSchemaValue:
        """Convert a type to a JSON schema

        Args:
            t (Type): The type to convert

        Returns:
            JsonSchemaValue: The JSON schema
        """
        if PYDANTIC_V1:
            if t is None:
                return {"type": "null"}
            elif get_origin(t) is Union:
                return {"anyOf": [type2schema(tt) for tt in get_args(t)]}
            elif get_origin(t) in [Tuple, tuple]:
                prefixItems = [type2schema(tt) for tt in get_args(t)]
                return {
                    "maxItems": len(prefixItems),
                    "minItems": len(prefixItems),
                    "prefixItems": prefixItems,
                    "type": "array",
                }

        d = TypeAdapter.json_schema(t)
        if "title" in d:
            d.pop("title")
        if "description" in d:
            d.pop("description")

        return d

    def model_dump(model: BaseModel) -> Dict[str, Any]:
        """Convert a pydantic model to a dict

        Args:
            model (BaseModel): The model to convert

        Returns:
            Dict[str, Any]: The dict representation of the model

        """
        return model.dict()

    def model_dump_json(model: BaseModel) -> str:
        """Convert a pydantic model to a JSON string

        Args:
            model (BaseModel): The model to convert

        Returns:
            str: The JSON string representation of the model
        """
        return model.json()

```

# jobber_fsm/utils/cli_helper.py

```py
import asyncio
from asyncio import Future
from typing import Dict, List


def async_input(prompt: str) -> Future:  # type: ignore
    """
    Display a prompt to the user and wait for input in an asynchronous manner.

    Parameters:
    - prompt: The message to display to the user.

    Returns:
    - A Future object that will be fulfilled with the user's input.
    """
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, input, prompt)


async def answer_questions_over_cli(questions: List[str]) -> Dict[str, str]:
    """
    Asks a question over the command line and awaits the user's response.

    Parameters:
    - questions: A list of questions to ask the user, e.g., ["What is your favorite site?", "What do you want to search for?"].

    Returns:
    - A dictionary where each key is a question and each value is the user's response.
    """
    answers: Dict[str, str] = {}
    print("*********************************")
    for question in questions:
        answers[question] = await async_input("Question: " + str(question) + " : ")
    print("*********************************")
    return answers

```

# jobber_fsm/utils/dom_helper.py

```py
import asyncio
from typing import List, Optional

from playwright.async_api import ElementHandle, Page

from jobber_fsm.utils.logger import logger


async def wait_for_non_loading_dom_state(page: Page, max_wait_millis: int):
    max_wait_seconds = max_wait_millis / 1000
    end_time = asyncio.get_event_loop().time() + max_wait_seconds
    while asyncio.get_event_loop().time() < end_time:
        dom_state = await page.evaluate("document.readyState")
        if dom_state != "loading":
            logger.debug(f"DOM state is not 'loading': {dom_state}")
            break  # Exit the loop if the DOM state is not 'loading'

        await asyncio.sleep(0.05)


async def get_element_outer_html(
    element: ElementHandle, page: Page, element_tag_name: Optional[str] = None
) -> str:
    """
    Constructs the opening tag of an HTML element along with its attributes.

    Args:
        element (ElementHandle): The element to retrieve the opening tag for.
        page (Page): The page object associated with the element.
        element_tag_name (str, optional): The tag name of the element. Defaults to None. If not passed, it will be retrieved from the element.

    Returns:
        str: The opening tag of the HTML element, including a select set of attributes.
    """
    tag_name: str = (
        element_tag_name
        if element_tag_name
        else await page.evaluate("element => element.tagName.toLowerCase()", element)
    )

    attributes_of_interest: List[str] = [
        "id",
        "name",
        "aria-label",
        "placeholder",
        "href",
        "src",
        "aria-autocomplete",
        "role",
        "type",
        "data-testid",
        "value",
        "selected",
        "aria-labelledby",
        "aria-describedby",
        "aria-haspopup",
    ]
    opening_tag: str = f"<{tag_name}"

    for attr in attributes_of_interest:
        value: str = await element.get_attribute(attr)  # type: ignore
        if value:
            opening_tag += f' {attr}="{value}"'
    opening_tag += ">"

    return opening_tag

```

# jobber_fsm/utils/dom_mutation_observer.py

```py
import asyncio
import json
from typing import Callable, List  # noqa: UP035

from playwright.async_api import Page

# Create an event loop
loop = asyncio.get_event_loop()

DOM_change_callback: List[Callable[[str], None]] = []


def subscribe(callback: Callable[[str], None]) -> None:
    DOM_change_callback.append(callback)


def unsubscribe(callback: Callable[[str], None]) -> None:
    DOM_change_callback.remove(callback)


async def add_mutation_observer(page: Page):
    """
    Adds a mutation observer to the page to detect changes in the DOM.
    When changes are detected, the observer calls the dom_mutation_change_detected function in the browser context.
    This changes can be detected by subscribing to the dom_mutation_change_detected function by individual skills.

    Current implementation only detects when a new node is added to the DOM.
    However, in many cases, the change could be a change in the style or class of an existing node (e.g. toggle visibility of a hidden node).
    """

    await page.evaluate("""
        console.log('Adding a mutation observer for DOM changes');
        new MutationObserver((mutationsList, observer) => {
            let changes_detected = [];
            for(let mutation of mutationsList) {
                if (mutation.type === 'childList') {
                    let allAddedNodes=mutation.addedNodes;
                    for(let node of allAddedNodes) {
                        if(node.tagName && !['SCRIPT', 'NOSCRIPT', 'STYLE'].includes(node.tagName) && !node.closest('#agentDriveAutoOverlay')) {
                            let visibility=true;
                            let content = node.innerText.trim();
                            if(visibility && node.innerText.trim()){
                                if(content) {
                                    changes_detected.push({tag: node.tagName, content: content});
                                }
                            }
                        }
                    }
                } else if (mutation.type === 'characterData') {
                    let node = mutation.target;
                    if(node.parentNode && !['SCRIPT', 'NOSCRIPT', 'STYLE'].includes(node.parentNode.tagName) && !node.parentNode.closest('#agentDriveAutoOverlay')) {
                        let visibility=true;
                        let content = node.data.trim();
                        if(visibility && content && window.getComputedStyle(node.parentNode).display !== 'none'){
                            if(content && !changes_detected.some(change => change.content.includes(content))) {
                                changes_detected.push({tag: node.parentNode.tagName, content: content});
                            }
                        }
                    }
                }
            }
            if(changes_detected.length > 0) {
                window.dom_mutation_change_detected(JSON.stringify(changes_detected));
            }
        }).observe(document, {subtree: true, childList: true, characterData: true});
        """)


async def handle_navigation_for_mutation_observer(page: Page):
    await add_mutation_observer(page)


async def dom_mutation_change_detected(changes_detected: str):
    """
    Detects changes in the DOM (new nodes added) and emits the event to all subscribed callbacks.
    The changes_detected is a string in JSON formatt containing the tag and content of the new nodes added to the DOM.

    e.g.  The following will be detected when autocomplete recommendations show up when one types Nelson Mandela on google search
    [{'tag': 'SPAN', 'content': 'nelson mandela wikipedia'}, {'tag': 'SPAN', 'content': 'nelson mandela movies'}]
    """
    changes_detected = json.loads(changes_detected.replace("\t", "").replace("\n", ""))
    if len(changes_detected) > 0:
        # Emit the event to all subscribed callbacks
        for callback in DOM_change_callback:
            # If the callback is a coroutine function
            if asyncio.iscoroutinefunction(callback):
                await callback(changes_detected)
            # If the callback is a regular function
            else:
                callback(changes_detected)

```

# jobber_fsm/utils/extract_json.py

```py
import json
from typing import Any, Dict

from jobber.utils.logger import logger


def extract_json(message: str) -> Dict[str, Any]:
    """
    Parse the response from the browser agent and return the response as a dictionary.
    """
    json_response = {}
    # Remove Markdown code block delimiters if present
    message = message.strip()
    if message.startswith("\`\`\`"):
        message = message.split("\n", 1)[1]  # Remove the first line
    if message.endswith("\`\`\`"):
        message = message.rsplit("\n", 1)[0]  # Remove the last line

    # Remove any leading "json" tag
    if message.lstrip().startswith("json"):
        message = message.lstrip()[4:].lstrip()

    try:
        return json.loads(message)
    except json.JSONDecodeError as e:
        logger.warn(
            f"LLM response was not properly formed JSON. Error: {e}. "
            f'LLM response: "{message}"'
        )
        message = message.replace("\\n", "\n")
        message = message.replace("\n", " ")  # type: ignore
        if "plan" in message and "next_step" in message:
            start = message.index("plan") + len("plan")
            end = message.index("next_step")
            json_response["plan"] = message[start:end].replace('"', "").strip()
        if "next_step" in message and "terminate" in message:
            start = message.index("next_step") + len("next_step")
            end = message.index("terminate")
            json_response["next_step"] = message[start:end].replace('"', "").strip()
        if "terminate" in message and "final_response" in message:
            start = message.index("terminate") + len("terminate")
            end = message.index("final_response")
            matched_string = message[start:end].replace('"', "").strip()
            if "yes" in matched_string:
                json_response["terminate"] = "yes"
            else:
                json_response["terminate"] = "no"

            start = message.index("final_response") + len("final_response")
            end = len(message) - 1
            json_response["final_response"] = (
                message[start:end].replace('"', "").strip()
            )

        elif "terminate" in message:
            start = message.index("terminate") + len("terminate")
            end = len(message) - 1
            matched_string = message[start:end].replace('"', "").strip()
            if "yes" in matched_string:
                json_response["terminate"] = "yes"
            else:
                json_response["terminate"] = "no"

    return json_response

```

# jobber_fsm/utils/function_utils.py

```py
# import inspect
# from typing import Any, Callable, Dict, List, Union

# from typing_extensions import Annotated, get_args, get_origin


# def get_type_name(type_hint: Any) -> str:
#     if hasattr(type_hint, "__name__"):
#         return type_hint.__name__
#     if hasattr(type_hint, "_name"):
#         return type_hint._name
#     return str(type_hint).replace("typing.", "")


# def get_parameter_schema(
#     name: str, param: inspect.Parameter, type_hint: Any
# ) -> Dict[str, Any]:
#     schema = {"type": get_type_name(type_hint)}

#     if get_origin(type_hint) is Annotated:
#         type_hint, description = get_args(type_hint)
#         schema["description"] = description
#     else:
#         schema["description"] = name

#     if get_origin(type_hint) is Union:
#         schema["type"] = [get_type_name(arg) for arg in get_args(type_hint)]
#     elif get_origin(type_hint) is List:
#         item_type = get_args(type_hint)[0]
#         if get_origin(item_type) is Dict:
#             key_type, value_type = get_args(item_type)
#             schema["type"] = "array"
#             schema["items"] = {
#                 "type": "object",
#                 "additionalProperties": {"type": get_type_name(value_type)},
#             }
#         else:
#             schema["type"] = "array"
#             schema["items"] = {"type": get_type_name(item_type)}

#     if param.default != inspect.Parameter.empty:
#         schema["default"] = param.default
#     return schema


# def generate_tool_from_function(
#     func: Callable[..., Any], tool_description: str
# ) -> Dict[str, Any]:
#     signature = inspect.signature(func)
#     type_hints = func.__annotations__

#     parameters = {}
#     for name, param in signature.parameters.items():
#         type_hint = type_hints.get(name, Any)
#         parameters[name] = get_parameter_schema(name, param, type_hint)

#     return {
#         "type": "function",
#         "function": {
#             "name": func.__name__,
#             "description": tool_description,
#             "parameters": {
#                 "type": "object",
#                 "properties": parameters,
#                 "required": [
#                     name
#                     for name, param in signature.parameters.items()
#                     if param.default == inspect.Parameter.empty
#                 ],
#             },
#         },
#     }


import functools
import inspect
import json
from logging import getLogger
from typing import (
    Any,
    Callable,
    Dict,
    ForwardRef,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    TypeVar,
    Union,
)

from pydantic import BaseModel, Field
from typing_extensions import Annotated, Literal, get_args, get_origin

from ._pydantic import (
    JsonSchemaValue,
    evaluate_forwardref,
    model_dump,
    model_dump_json,
    type2schema,
)

logger = getLogger(__name__)

T = TypeVar("T")


def get_typed_annotation(annotation: Any, globalns: Dict[str, Any]) -> Any:
    """Get the type annotation of a parameter.

    Args:
        annotation: The annotation of the parameter
        globalns: The global namespace of the function

    Returns:
        The type annotation of the parameter
    """
    if isinstance(annotation, str):
        annotation = ForwardRef(annotation)
        annotation = evaluate_forwardref(annotation, globalns, globalns)
    return annotation


def get_typed_signature(call: Callable[..., Any]) -> inspect.Signature:
    """Get the signature of a function with type annotations.

    Args:
        call: The function to get the signature for

    Returns:
        The signature of the function with type annotations
    """
    signature = inspect.signature(call)
    globalns = getattr(call, "__globals__", {})
    typed_params = [
        inspect.Parameter(
            name=param.name,
            kind=param.kind,
            default=param.default,
            annotation=get_typed_annotation(param.annotation, globalns),
        )
        for param in signature.parameters.values()
    ]
    typed_signature = inspect.Signature(typed_params)
    return typed_signature


def get_typed_return_annotation(call: Callable[..., Any]) -> Any:
    """Get the return annotation of a function.

    Args:
        call: The function to get the return annotation for

    Returns:
        The return annotation of the function
    """
    signature = inspect.signature(call)
    annotation = signature.return_annotation

    if annotation is inspect.Signature.empty:
        return None

    globalns = getattr(call, "__globals__", {})
    return get_typed_annotation(annotation, globalns)


def get_param_annotations(
    typed_signature: inspect.Signature,
) -> Dict[str, Union[Annotated[Type[Any], str], Type[Any]]]:
    """Get the type annotations of the parameters of a function

    Args:
        typed_signature: The signature of the function with type annotations

    Returns:
        A dictionary of the type annotations of the parameters of the function
    """
    return {
        k: v.annotation
        for k, v in typed_signature.parameters.items()
        if v.annotation is not inspect.Signature.empty
    }


class Parameters(BaseModel):
    """Parameters of a function as defined by the OpenAI API"""

    type: Literal["object"] = "object"
    properties: Dict[str, JsonSchemaValue]
    required: List[str]
    additionalProperties: bool
    additionalProperties: bool


class Function(BaseModel):
    """A function as defined by the OpenAI API"""

    description: Annotated[str, Field(description="Description of the function")]
    name: Annotated[str, Field(description="Name of the function")]
    parameters: Annotated[Parameters, Field(description="Parameters of the function")]
    strict: bool


class ToolFunction(BaseModel):
    """A function under tool as defined by the OpenAI API."""

    type: Literal["function"] = "function"
    function: Annotated[Function, Field(description="Function under tool")]


def get_parameter_json_schema(
    k: str, v: Any, default_values: Dict[str, Any]
) -> JsonSchemaValue:
    def type2description(k: str, v: Union[Annotated[Type[Any], str], Type[Any]]) -> str:
        if get_origin(v) is Annotated:
            args = get_args(v)
            if len(args) > 1 and isinstance(args[1], str):
                return args[1]
        return k

    schema = type2schema(v)
    schema["description"] = type2description(k, v)

    if schema["type"] == "object":
        schema["additionalProperties"] = False
        if "properties" not in schema:
            schema["properties"] = {}

    if schema["type"] == "array":
        if "items" not in schema:
            schema["items"] = {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }
        elif schema["items"].get("type") == "object":
            if "properties" not in schema["items"]:
                schema["items"]["properties"] = {}
            schema["items"]["additionalProperties"] = False

    return schema


def get_required_params(typed_signature: inspect.Signature) -> List[str]:
    """Get the required parameters of a function

    Args:
        signature: The signature of the function as returned by inspect.signature

    Returns:
        A list of the required parameters of the function
    """
    return [
        k
        for k, v in typed_signature.parameters.items()
        if v.default == inspect.Signature.empty
    ]


def get_default_values(typed_signature: inspect.Signature) -> Dict[str, Any]:
    """Get default values of parameters of a function

    Args:
        signature: The signature of the function as returned by inspect.signature

    Returns:
        A dictionary of the default values of the parameters of the function
    """
    return {
        k: v.default
        for k, v in typed_signature.parameters.items()
        if v.default != inspect.Signature.empty
    }


def get_parameters(
    required: List[str],
    param_annotations: Dict[str, Union[Annotated[Type[Any], str], Type[Any]]],
    default_values: Dict[str, Any],
) -> Parameters:
    properties = {}
    for k, v in param_annotations.items():
        if v is not inspect.Signature.empty:
            if get_origin(v) is Annotated:
                v_type = get_args(v)[0]
                v_desc = get_args(v)[1] if len(get_args(v)) > 1 else k
            else:
                v_type = v
                v_desc = k

            if get_origin(v_type) is List:
                item_type = get_args(v_type)[0]
                properties[k] = {
                    "type": "array",
                    "items": get_parameter_json_schema(k, item_type, default_values),
                    "description": v_desc,
                }
            else:
                properties[k] = get_parameter_json_schema(k, v_type, default_values)
                properties[k]["description"] = v_desc

    return Parameters(
        properties=properties,
        required=list(properties.keys()),  # All properties are required
        additionalProperties=False,
    )


def get_missing_annotations(
    typed_signature: inspect.Signature, required: List[str]
) -> Tuple[Set[str], Set[str]]:
    """Get the missing annotations of a function

    Ignores the parameters with default values as they are not required to be annotated, but logs a warning.
    Args:
        typed_signature: The signature of the function with type annotations
        required: The required parameters of the function

    Returns:
        A set of the missing annotations of the function
    """
    all_missing = {
        k
        for k, v in typed_signature.parameters.items()
        if v.annotation is inspect.Signature.empty
    }
    missing = all_missing.intersection(set(required))
    unannotated_with_default = all_missing.difference(missing)
    return missing, unannotated_with_default


def get_function_schema(
    f: Callable[..., Any], *, name: Optional[str] = None, description: str
) -> Dict[str, Any]:
    """Get a JSON schema for a function as defined by the OpenAI API

    Args:
        f: The function to get the JSON schema for
        name: The name of the function
        description: The description of the function

    Returns:
        A JSON schema for the function

    Raises:
        TypeError: If the function is not annotated

    Examples:

    \`\`\`python
    def f(a: Annotated[str, "Parameter a"], b: int = 2, c: Annotated[float, "Parameter c"] = 0.1) -> None:
        pass

    get_function_schema(f, description="function f")

    #   {'type': 'function',
    #    'function': {'description': 'function f',
    #        'name': 'f',
    #        'parameters': {'type': 'object',
    #           'properties': {'a': {'type': 'str', 'description': 'Parameter a'},
    #               'b': {'type': 'int', 'description': 'b'},
    #               'c': {'type': 'float', 'description': 'Parameter c'}},
    #           'required': ['a']}}}
    \`\`\`

    """
    typed_signature = get_typed_signature(f)
    required = get_required_params(typed_signature)
    default_values = get_default_values(typed_signature)
    param_annotations = get_param_annotations(typed_signature)
    return_annotation = get_typed_return_annotation(f)
    missing, unannotated_with_default = get_missing_annotations(
        typed_signature, required
    )

    if return_annotation is None:
        logger.warning(
            f"The return type of the function '{f.__name__}' is not annotated. Although annotating it is "
            + "optional, the function should return either a string, a subclass of 'pydantic.BaseModel'."
        )

    if unannotated_with_default != set():
        unannotated_with_default_s = [
            f"'{k}'" for k in sorted(unannotated_with_default)
        ]
        logger.warning(
            f"The following parameters of the function '{f.__name__}' with default values are not annotated: "
            + f"{', '.join(unannotated_with_default_s)}."
        )

    if missing != set():
        missing_s = [f"'{k}'" for k in sorted(missing)]
        raise TypeError(
            f"All parameters of the function '{f.__name__}' without default values must be annotated. "
            + f"The annotations are missing for the following parameters: {', '.join(missing_s)}"
        )

    fname = name if name else f.__name__

    parameters = get_parameters(
        required, param_annotations, default_values=default_values
    )

    function = ToolFunction(
        function=Function(
            description=description,
            name=fname,
            parameters=parameters,
            strict=True,
        )
    )

    schema = model_dump(function)

    return schema


def get_load_param_if_needed_function(
    t: Any,
) -> Optional[Callable[[Dict[str, Any], Type[BaseModel]], BaseModel]]:
    """Get a function to load a parameter if it is a Pydantic model

    Args:
        t: The type annotation of the parameter

    Returns:
        A function to load the parameter if it is a Pydantic model, otherwise None

    """
    if get_origin(t) is Annotated:
        return get_load_param_if_needed_function(get_args(t)[0])

    def load_base_model(v: Dict[str, Any], t: Type[BaseModel]) -> BaseModel:
        return t(**v)

    return load_base_model if isinstance(t, type) and issubclass(t, BaseModel) else None


def load_basemodels_if_needed(func: Callable[..., Any]) -> Callable[..., Any]:
    """A decorator to load the parameters of a function if they are Pydantic models

    Args:
        func: The function with annotated parameters

    Returns:
        A function that loads the parameters before calling the original function

    """
    # get the type annotations of the parameters
    typed_signature = get_typed_signature(func)
    param_annotations = get_param_annotations(typed_signature)

    # get functions for loading BaseModels when needed based on the type annotations
    kwargs_mapping_with_nones = {
        k: get_load_param_if_needed_function(t) for k, t in param_annotations.items()
    }

    # remove the None values
    kwargs_mapping = {
        k: f for k, f in kwargs_mapping_with_nones.items() if f is not None
    }

    # a function that loads the parameters before calling the original function
    @functools.wraps(func)
    def _load_parameters_if_needed(*args: Any, **kwargs: Any) -> Any:
        # load the BaseModels if needed
        for k, f in kwargs_mapping.items():
            kwargs[k] = f(kwargs[k], param_annotations[k])

        # call the original function
        return func(*args, **kwargs)

    @functools.wraps(func)
    async def _a_load_parameters_if_needed(*args: Any, **kwargs: Any) -> Any:
        # load the BaseModels if needed
        for k, f in kwargs_mapping.items():
            kwargs[k] = f(kwargs[k], param_annotations[k])

        # call the original function
        return await func(*args, **kwargs)

    if inspect.iscoroutinefunction(func):
        return _a_load_parameters_if_needed
    else:
        return _load_parameters_if_needed


def serialize_to_str(x: Any) -> str:
    if isinstance(x, str):
        return x
    elif isinstance(x, BaseModel):
        return model_dump_json(x)
    else:
        return json.dumps(x)

```

# jobber_fsm/utils/get_detailed_accessibility_tree.py

```py
import json
import os
import re
import traceback
from typing import Dict, List, Optional

from playwright.async_api import Page
from typing_extensions import Annotated, Any

from jobber_fsm.config.config import SOURCE_LOG_FOLDER_PATH
from jobber_fsm.core.web_driver.playwright import PlaywrightManager
from jobber_fsm.utils.logger import logger

space_delimited_mmid = re.compile(r"^[\d ]+$")


def is_space_delimited_mmid(s: str) -> bool:
    """
    Check if the given string matches the the mmid pattern of number space repeated.

    Parameters:
    - s (str): The string to check against the pattern.

    Returns:
    - bool: True if the string matches the pattern, False otherwise.
    """
    # Use fullmatch() to ensure the entire string matches the pattern
    return bool(space_delimited_mmid.fullmatch(s))


async def __inject_attributes(page: Page):
    """
    Injects 'mmid' and 'aria-keyshortcuts' into all DOM elements. If an element already has an 'aria-keyshortcuts',
    it renames it to 'orig-aria-keyshortcuts' before injecting the new 'aria-keyshortcuts'
    This will be captured in the accessibility tree and thus make it easier to reconcile the tree with the DOM.
    'aria-keyshortcuts' is choosen because it is not widely used aria attribute.
    """

    last_mmid = await page.evaluate("""() => {
        const allElements = document.querySelectorAll('*');
        let id = 0;
        allElements.forEach(element => {
            const origAriaAttribute = element.getAttribute('aria-keyshortcuts');
            const mmid = `${++id}`;
            element.setAttribute('mmid', mmid);
            element.setAttribute('aria-keyshortcuts', mmid);
            //console.log(`Injected 'mmid'into element with tag: ${element.tagName} and mmid: ${mmid}`);
            if (origAriaAttribute) {
                element.setAttribute('orig-aria-keyshortcuts', origAriaAttribute);
            }
        });
        return id;
    }""")
    logger.debug(f"Added MMID into {last_mmid} elements")


async def __fetch_dom_info(
    page: Page, accessibility_tree: Dict[str, Any], only_input_fields: bool
):
    """
    Iterates over the accessibility tree, fetching additional information from the DOM based on 'mmid',
    and constructs a new JSON structure with detailed information.

    Args:
        page (Page): The page object representing the web page.
        accessibility_tree (Dict[str, Any]): The accessibility tree JSON structure.
        only_input_fields (bool): Flag indicating whether to include only input fields in the new JSON structure.

    Returns:
        Dict[str, Any]: The pruned tree with detailed information from the DOM.
    """

    logger.debug("Reconciling the Accessibility Tree with the DOM")
    # Define the attributes to fetch for each element
    attributes = [
        "name",
        "aria-label",
        "placeholder",
        "mmid",
        "id",
        "for",
        "data-testid",
    ]
    backup_attributes = []  # if the attributes are not found, then try to get these attributes
    tags_to_ignore = [
        "head",
        "style",
        "script",
        "link",
        "meta",
        "noscript",
        "template",
        "iframe",
        "g",
        "main",
        "c-wiz",
        "svg",
        "path",
    ]
    attributes_to_delete = ["level", "multiline", "haspopup", "id", "for"]
    ids_to_ignore = ["agentDriveAutoOverlay"]

    # Recursive function to process each node in the accessibility tree
    async def process_node(node: Dict[str, Any]):
        if "children" in node:
            for child in node["children"]:
                await process_node(child)

        # Use 'name' attribute from the accessibility node as 'mmid'
        mmid_temp: str = node.get("keyshortcuts")  # type: ignore

        # If the name has multiple mmids, take the last one
        if mmid_temp and is_space_delimited_mmid(mmid_temp):
            # TODO: consider if we should grab each of the mmids and process them separately as seperate nodes copying this node's attributes
            mmid_temp = mmid_temp.split(" ")[-1]

        # focusing on nodes with mmid, which is the attribute we inject
        try:
            mmid = int(mmid_temp)
        except (ValueError, TypeError):
            # logger.error(f"'name attribute contains \"{node.get('name')}\", which is not a valid numeric mmid. Adding node as is: {node}")
            return node.get("name")

        if node["role"] == "menuitem":
            return node.get("name")

        if node.get("role") == "dialog" and node.get("modal") == True:  # noqa: E712
            node["important information"] = (
                "This is a modal dialog. Please interact with this dialog and close it to be able to interact with the full page (e.g. by pressing the close button or selecting an option)."
            )

        if mmid:
            # Determine if we need to fetch 'innerText' based on the absence of 'children' in the accessibility node
            should_fetch_inner_text = "children" not in node

            js_code = """
            (input_params) => {
                const should_fetch_inner_text = input_params.should_fetch_inner_text;
                const mmid = input_params.mmid;
                const attributes = input_params.attributes;
                const tags_to_ignore = input_params.tags_to_ignore;
                const ids_to_ignore = input_params.ids_to_ignore;

                const element = document.querySelector(`[mmid="${mmid}"]`);

                if (!element) {
                    console.log(`No element found with mmid: ${mmid}`);
                    return null;
                }

                if (ids_to_ignore.includes(element.id)) {
                    console.log(`Ignoring element with id: ${element.id}`, element);
                    return null;
                }
                //Ignore "option" because it would have been processed with the select element
                if (tags_to_ignore.includes(element.tagName.toLowerCase()) || element.tagName.toLowerCase() === "option") return null;

                let attributes_to_values = {
                    'tag': element.tagName.toLowerCase() // Always include the tag name
                };

                // If the element is an input, include its type as well
                if (element.tagName.toLowerCase() === 'input') {
                    attributes_to_values['tag_type'] = element.type; // This will capture 'checkbox', 'radio', etc.
                }
                else if (element.tagName.toLowerCase() === 'select') {
                    attributes_to_values["mmid"] = element.getAttribute('mmid');
                    attributes_to_values["role"] = "combobox";
                    attributes_to_values["options"] = [];

                    for (const option of element.options) {
                        let option_attributes_to_values = {
                            "mmid": option.getAttribute('mmid'),
                            "text": option.text,
                            "value": option.value,
                            "selected": option.selected
                        };
                        attributes_to_values["options"].push(option_attributes_to_values);
                    }
                    return attributes_to_values;
                }

                for (const attribute of attributes) {
                    let value = element.getAttribute(attribute);

                    if(value){
                        /*
                        if(attribute === 'href'){
                            value = value.split('?')[0]
                        }
                        */
                        attributes_to_values[attribute] = value;
                    }
                }

                if (should_fetch_inner_text && element.innerText) {
                    attributes_to_values['description'] = element.innerText;
                }

                let role = element.getAttribute('role');
                if(role==='listbox' || element.tagName.toLowerCase()=== 'ul'){
                    let children=element.children;
                    let filtered_children = Array.from(children).filter(child => child.getAttribute('role') === 'option');
                    console.log("Listbox or ul found: ", filtered_children);
                    let attributes_to_include = ['mmid', 'role', 'aria-label','value'];
                    attributes_to_values["additional_info"]=[]
                    for (const child of children) {
                        let children_attributes_to_values = {};

                        for (let attr of child.attributes) {
                            // If the attribute is not in the predefined list, add it to children_attributes_to_values
                            if (attributes_to_include.includes(attr.name)) {
                                children_attributes_to_values[attr.name] = attr.value;
                            }
                        }

                        attributes_to_values["additional_info"].push(children_attributes_to_values);
                    }
                }
                // Check if attributes_to_values contains more than just 'name', 'role', and 'mmid'
                const keys = Object.keys(attributes_to_values);
                const minimalKeys = ['tag', 'mmid'];
                const hasMoreThanMinimalKeys = keys.length > minimalKeys.length || keys.some(key => !minimalKeys.includes(key));

                if (!hasMoreThanMinimalKeys) {
                    //If there were no attributes found, then try to get the backup attributes
                    for (const backupAttribute of input_params.backup_attributes) {
                        let value = element.getAttribute(backupAttribute);
                        if(value){
                            attributes_to_values[backupAttribute] = value;
                        }
                    }

                    //if even the backup attributes are not found, then return null, which will cause this element to be skipped
                    if(Object.keys(attributes_to_values).length <= minimalKeys.length) {
                        if (element.tagName.toLowerCase() === 'button') {
                                attributes_to_values["mmid"] = element.getAttribute('mmid');
                                attributes_to_values["role"] = "button";
                                attributes_to_values["additional_info"] = [];
                                let children=element.children;
                                let attributes_to_exclude = ['width', 'height', 'path', 'class', 'viewBox', 'mmid']

                                // Check if the button has no text and no attributes
                                if (element.innerText.trim() === '') {

                                    for (const child of children) {
                                        let children_attributes_to_values = {};

                                        for (let attr of child.attributes) {
                                            // If the attribute is not in the predefined list, add it to children_attributes_to_values
                                            if (!attributes_to_exclude.includes(attr.name)) {
                                                children_attributes_to_values[attr.name] = attr.value;
                                            }
                                        }

                                        attributes_to_values["additional_info"].push(children_attributes_to_values);
                                    }
                                    console.log("Button with no text and no attributes: ", attributes_to_values);
                                    return attributes_to_values;
                                }
                        }

                        return null; // Return null if only minimal keys are present
                    }
                }
                return attributes_to_values;
            }
            """

            # Fetch attributes and possibly 'innerText' from the DOM element by 'mmid'
            element_attributes = await page.evaluate(
                js_code,
                {
                    "mmid": mmid,
                    "attributes": attributes,
                    "backup_attributes": backup_attributes,
                    "should_fetch_inner_text": should_fetch_inner_text,
                    "tags_to_ignore": tags_to_ignore,
                    "ids_to_ignore": ids_to_ignore,
                },
            )

            if "keyshortcuts" in node:
                del node["keyshortcuts"]  # remove keyshortcuts since it is not needed

            node["mmid"] = mmid

            # Update the node with fetched information
            if element_attributes:
                node.update(element_attributes)

                # check if 'name' and 'mmid' are the same
                if (
                    node.get("name") == node.get("mmid")
                    and node.get("role") != "textbox"
                ):
                    del node["name"]  # Remove 'name' from the node

                if (
                    "name" in node
                    and "description" in node
                    and (
                        node["name"] == node["description"]
                        or node["name"] == node["description"].replace("\n", " ")
                        or node["description"].replace("\n", "") in node["name"]
                    )
                ):
                    del node[
                        "description"
                    ]  # if the name is same as description, then remove the description to avoid duplication

                if (
                    "name" in node
                    and "aria-label" in node
                    and node["aria-label"] in node["name"]
                ):
                    del node[
                        "aria-label"
                    ]  # if the name is same as the aria-label, then remove the aria-label to avoid duplication

                if "name" in node and "text" in node and node["name"] == node["text"]:
                    del node[
                        "text"
                    ]  # if the name is same as the text, then remove the text to avoid duplication

                if (
                    node.get("tag") == "select"
                ):  # children are not needed for select menus since "options" attriburte is already added
                    node.pop("children", None)
                    node.pop("role", None)
                    node.pop("description", None)

                # role and tag can have the same info. Get rid of role if it is the same as tag
                if node.get("role") == node.get("tag"):
                    del node["role"]

                # avoid duplicate aria-label
                if (
                    node.get("aria-label")
                    and node.get("placeholder")
                    and node.get("aria-label") == node.get("placeholder")
                ):
                    del node["aria-label"]

                if node.get("role") == "link":
                    del node["role"]
                    if node.get("description"):
                        node["text"] = node["description"]
                        del node["description"]

                # textbox just means a text input and that is expressed well enough with the rest of the attributes returned
                # if node.get('role') == "textbox":
                #    del node['role']

                if node.get("role") == "textbox":
                    # get the id attribute of this field from the DOM
                    if "id" in element_attributes and element_attributes["id"]:
                        # find if there is an element in the DOM that has this id in aria-labelledby.
                        js_code = """
                        (inputParams) => {
                            let referencingElements = [];
                            const referencedElement = document.querySelector(`[aria-labelledby="${inputParams.aria_labelled_by_query_value}"]`);
                            if(referencedElement) {
                                const mmid = referencedElement.getAttribute('mmid');
                                if (mmid) {
                                    return {"mmid": mmid, "tag": referencedElement.tagName.toLowerCase()};
                                }
                            }
                            return null;
                        }
                        """
                    # textbox just means a text input and that is expressed well enough with the rest of the attributes returned
                    # del node['role']

            # remove attributes that are not needed once processing of a node is complete
            for attribute_to_delete in attributes_to_delete:
                if attribute_to_delete in node:
                    node.pop(attribute_to_delete, None)
        else:
            logger.debug(f"No element found with mmid: {mmid}, deleting node: {node}")
            node["marked_for_deletion_by_mm"] = True

    # Process each node in the tree starting from the root
    await process_node(accessibility_tree)

    pruned_tree = __prune_tree(accessibility_tree, only_input_fields)

    logger.debug("Reconciliation complete")
    return pruned_tree


async def __cleanup_dom(page: Page):
    """
    Cleans up the DOM by removing injected 'aria-description' attributes and restoring any original 'aria-keyshortcuts'
    from 'orig-aria-keyshortcuts'.
    """
    logger.debug("Cleaning up the DOM's previous injections")
    await page.evaluate("""() => {
        const allElements = document.querySelectorAll('*[mmid]');
        allElements.forEach(element => {
            element.removeAttribute('aria-keyshortcuts');
            const origAriaLabel = element.getAttribute('orig-aria-keyshortcuts');
            if (origAriaLabel) {
                element.setAttribute('aria-keyshortcuts', origAriaLabel);
                element.removeAttribute('orig-aria-keyshortcuts');
            }
        });
    }""")
    logger.debug("DOM cleanup complete")


def __prune_tree(
    node: Dict[str, Any], only_input_fields: bool
) -> Optional[Dict[str, Any]]:
    """
    Recursively prunes a tree starting from `node`, based on pruning conditions and handling of 'unraveling'.

    The function has two main jobs:
    1. Pruning: Remove nodes that don't meet certain conditions, like being marked for deletion.
    2. Unraveling: For nodes marked with 'marked_for_unravel_children', we replace them with their children,
       effectively removing the node and lifting its children up a level in the tree.

    This happens in place, meaning we modify the tree as we go, which is efficient but means you should
    be cautious about modifying the tree outside this function during a prune operation.

    Args:
    - node (Dict[str, Any]): The node we're currently looking at. We'll check this node, its children,
      and so on, recursively down the tree.
    - only_input_fields (bool): If True, we're only interested in pruning input-related nodes (like form fields).
      This lets you narrow the focus if, for example, you're only interested in cleaning up form-related parts
      of a larger tree.

    Returns:
    - Dict[str, Any] | None: The pruned version of `node`, or None if `node` was pruned away. When we 'unravel'
      a node, we directly replace it with its children in the parent's list of children, so the return value
      will be the parent, updated in place.

    Notes:
    - 'marked_for_deletion_by_mm' is our flag for nodes that should definitely be removed.
    - Unraveling is neat for flattening the tree when a node is just a wrapper without semantic meaning.
    - We use a while loop with manual index management to safely modify the list of children as we iterate over it.
    """
    if "marked_for_deletion_by_mm" in node:
        return None

    if "children" in node:
        i = 0
        while i < len(node["children"]):
            child = node["children"][i]
            if "marked_for_unravel_children" in child:
                # Replace the current child with its children
                if "children" in child:
                    node["children"] = (
                        node["children"][:i]
                        + child["children"]
                        + node["children"][i + 1 :]
                    )
                    i += (
                        len(child["children"]) - 1
                    )  # Adjust the index for the new children
                else:
                    # If the node marked for unraveling has no children, remove it
                    node["children"].pop(i)
                    i -= 1  # Adjust the index since we removed an element
            else:
                # Recursively prune the child if it's not marked for unraveling
                pruned_child = __prune_tree(child, only_input_fields)
                if pruned_child is None:
                    # If the child is pruned, remove it from the children list
                    node["children"].pop(i)
                    i -= 1  # Adjust the index since we removed an element
                else:
                    # Update the child with the pruned version
                    node["children"][i] = pruned_child
            i += 1  # Move to the next child

        # After processing all children, if the children array is empty, remove it
        if not node["children"]:
            del node["children"]

    # Apply existing conditions to decide if the current node should be pruned
    return None if __should_prune_node(node, only_input_fields) else node


def __should_prune_node(node: Dict[str, Any], only_input_fields: bool):
    """
    Determines if a node should be pruned based on its 'role' and 'element_attributes'.

    Args:
        node (Dict[str, Any]): The node to be evaluated.
        only_input_fields (bool): Flag indicating whether only input fields should be considered.

    Returns:
        bool: True if the node should be pruned, False otherwise.
    """
    # If the request is for only input fields and this is not an input field, then mark the node for prunning
    if (
        node.get("role") != "WebArea"
        and only_input_fields
        and not (
            node.get("tag") in ("input", "button", "textarea")
            or node.get("role") == "button"
        )
    ):
        return True

    if (
        node.get("role") == "generic"
        and "children" not in node
        and not ("name" in node and node.get("name"))
    ):  # The presence of 'children' is checked after potentially deleting it above
        return True

    if node.get("role") in ["separator", "LineBreak"]:
        return True
    processed_name = ""
    if "name" in node:
        processed_name: str = node.get("name")  # type: ignore
        processed_name = processed_name.replace(",", "")
        processed_name = processed_name.replace(":", "")
        processed_name = processed_name.replace("\n", "")
        processed_name = processed_name.strip()
        if len(processed_name) < 3:
            processed_name = ""

    # check if the node only have name and role, then delete that node
    if (
        len(node) == 2
        and "name" in node
        and "role" in node
        and not (node.get("role") == "text" and processed_name != "")
    ):
        return True
    return False


async def get_node_dom_element(page: Page, mmid: str):
    return await page.evaluate(
        """
        (mmid) => {
            return document.querySelector(`[mmid="${mmid}"]`);
        }
    """,
        mmid,
    )


async def get_element_attributes(page: Page, mmid: str, attributes: List[str]):
    return await page.evaluate(
        """
        (inputParams) => {
            const mmid = inputParams.mmid;
            const attributes = inputParams.attributes;
            const element = document.querySelector(`[mmid="${mmid}"]`);
            if (!element) return null;  // Return null if element is not found

            let attrs = {};
            for (let attr of attributes) {
                attrs[attr] = element.getAttribute(attr);
            }
            return attrs;
        }
    """,
        {"mmid": mmid, "attributes": attributes},
    )


async def get_dom_with_accessibility_info() -> (
    Annotated[
        Optional[Dict[str, Any]],
        "A minified representation of the HTML DOM for the current webpage",
    ]
):
    """
    Retrieves, processes, and minifies the Accessibility tree of the active page in a browser instance.
    Strictly follow the name and role tag for any interaction with the nodes.

    Returns:
    - The minified JSON content of the browser's active page.
    """
    logger.debug("Executing Get Accessibility Tree Command")
    # Create and use the PlaywrightManager
    browser_manager = PlaywrightManager(browser_type="chromium", headless=False)
    page = await browser_manager.get_current_page()
    if page is None:  # type: ignore
        raise ValueError("No active page found")

    return await do_get_accessibility_info(page)


async def do_get_accessibility_info(page: Page, only_input_fields: bool = False):
    """
    Retrieves the accessibility information of a web page and saves it as JSON files.

    Args:
        page (Page): The page object representing the web page.
        only_input_fields (bool, optional): If True, only retrieves accessibility information for input fields.
            Defaults to False.

    Returns:
        Dict[str, Any] or None: The enhanced accessibility tree as a dictionary, or None if an error occurred.
    """
    await __inject_attributes(page)
    accessibility_tree: Dict[str, Any] = await page.accessibility.snapshot(
        interesting_only=True
    )  # type: ignore

    with open(
        os.path.join(SOURCE_LOG_FOLDER_PATH, "json_accessibility_dom.json"),
        "w",
        encoding="utf-8",
    ) as f:
        f.write(json.dumps(accessibility_tree, indent=2))
        logger.debug("json_accessibility_dom.json saved")

    await __cleanup_dom(page)
    try:
        enhanced_tree = await __fetch_dom_info(
            page, accessibility_tree, only_input_fields
        )

        logger.debug("Enhanced Accessibility Tree ready")

        with open(
            os.path.join(
                SOURCE_LOG_FOLDER_PATH, "json_accessibility_dom_enriched.json"
            ),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(json.dumps(enhanced_tree, indent=2))
            logger.debug("json_accessibility_dom_enriched.json saved")

        return enhanced_tree
    except Exception as e:
        logger.error(f"Error while fetching DOM info: {e}")
        traceback.print_exc()
        return None

```

# jobber_fsm/utils/logger.py

```py
import logging
import os
from typing import Union

# Create a logs directory if it doesn't exist
log_directory = "logs"
os.makedirs(log_directory, exist_ok=True)

# Configure the root logger
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s] %(levelname)s {%(filename)s:%(lineno)d} - %(message)s",
)

# Remove all handlers from the root logger
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

logger = logging.getLogger(__name__)
logger.addHandler(logging.FileHandler(os.path.join(log_directory, "app.log")))
logger.setLevel(logging.INFO)

# logging.getLogger("httpcore").setLevel(logging.WARNING)
# logging.getLogger("httpx").setLevel(logging.WARNING)
# logging.getLogger("matplotlib.pyplot").setLevel(logging.WARNING)
# logging.getLogger("PIL.PngImagePlugin").setLevel(logging.WARNING)
# logging.getLogger("PIL.Image").setLevel(logging.WARNING)


def set_log_level(level: Union[str, int]) -> None:
    """
    Set the log level for the logger.

    Parameters:
    - level (Union[str, int]): A string or logging level such as 'debug', 'info', 'warning', 'error', or 'critical', or the corresponding logging constants like logging.DEBUG, logging.INFO, etc.
    """
    if isinstance(level, str):
        level = level.upper()
        numeric_level = getattr(logging, level, None)
        if not isinstance(numeric_level, int):
            raise ValueError(f"Invalid log level: {level}")
        logger.setLevel(numeric_level)
    else:
        logger.setLevel(level)

```

# jobber_fsm/utils/message_type.py

```py
from enum import Enum


class MessageType(Enum):
    PLAN = "plan"
    STEP = "step"
    ACTION = "action"
    ANSWER = "answer"
    QUESTION = "question"
    INFO = "info"
    FINAL = "final"
    DONE = "transaction_done"
    ERROR = "error"

```

# jobber_fsm/utils/screenshot_debugger.py

```py
"""
Screenshot helper for debugging Cloud Run executions
"""
import os
import base64
from datetime import datetime
from typing import Optional
from google.cloud import storage
from playwright.async_api import Page
from jobber_fsm.utils.logger import logger

class ScreenshotDebugger:
    def __init__(self, job_id: str, bucket_name: str = 'jobber-resumes-prod'):
        self.job_id = job_id
        self.bucket_name = bucket_name
        self.screenshot_count = 0
        self.storage_client = storage.Client()
        self.bucket = self.storage_client.bucket(bucket_name)
        
    async def capture(self, page: Page, step_name: str, description: str = "") -> Optional[str]:
        """
        Capture a screenshot and upload to GCS
        
        Args:
            page: Playwright page object
            step_name: Name of the current step (e.g., "after_login", "form_filled")
            description: Optional description of what should be visible
            
        Returns:
            GCS path of the uploaded screenshot
        """
        logger.info(f"[ScreenshotDebugger] Attempting to capture screenshot: {step_name}")
        logger.info(f"[ScreenshotDebugger] Job ID: {self.job_id}, Count: {self.screenshot_count}")
        
        try:
            self.screenshot_count += 1
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            
            logger.info(f"[ScreenshotDebugger] Waiting for page to stabilize...")
            # Wait a bit for page to stabilize
            await page.wait_for_load_state('networkidle', timeout=5000)
            
            logger.info(f"[ScreenshotDebugger] Taking screenshot...")
            # Take screenshot
            screenshot_bytes = await page.screenshot(full_page=True)
            logger.info(f"[ScreenshotDebugger] Screenshot taken, size: {len(screenshot_bytes)} bytes")
            
            # Also capture page info
            page_info = {
                'url': page.url,
                'title': await page.title(),
                'timestamp': timestamp,
                'step': step_name,
                'description': description,
                'viewport': await page.viewport_size(),
            }
            
            # Try to capture any form data (for debugging what was entered)
            try:
                form_data = await page.evaluate('''() => {
                    const inputs = document.querySelectorAll('input, select, textarea');
                    const data = {};
                    inputs.forEach(input => {
                        if (input.name || input.id) {
                            data[input.name || input.id] = {
                                value: input.value,
                                type: input.type,
                                placeholder: input.placeholder,
                                required: input.required
                            };
                        }
                    });
                    return data;
                }''')
                page_info['form_data'] = form_data
            except:
                pass
            
            # File paths
            screenshot_filename = f"{self.screenshot_count:03d}_{step_name}_{timestamp}.png"
            screenshot_path = f"debug/jobs/{self.job_id}/screenshots/{screenshot_filename}"
            
            info_filename = f"{self.screenshot_count:03d}_{step_name}_{timestamp}.json"
            info_path = f"debug/jobs/{self.job_id}/screenshots/{info_filename}"
            
            # Upload screenshot
            screenshot_blob = self.bucket.blob(screenshot_path)
            screenshot_blob.upload_from_string(
                screenshot_bytes,
                content_type='image/png'
            )
            
            # Upload page info
            import json
            info_blob = self.bucket.blob(info_path)
            info_blob.upload_from_string(
                json.dumps(page_info, indent=2),
                content_type='application/json'
            )
            
            # Generate a signed URL for easy viewing (valid for 1 hour)
            signed_url = screenshot_blob.generate_signed_url(
                version="v4",
                expiration=3600,  # 1 hour
                method="GET"
            )
            
            logger.info(f"Screenshot captured: {step_name}")
            logger.info(f"  View at: {signed_url}")
            logger.info(f"  GCS path: gs://{self.bucket_name}/{screenshot_path}")
            
            return f"gs://{self.bucket_name}/{screenshot_path}"
            
        except Exception as e:
            logger.error(f"Failed to capture screenshot for {step_name}: {e}")
            return None
    
    async def capture_with_highlight(self, page: Page, step_name: str, selector: str = None, description: str = "") -> Optional[str]:
        """
        Capture screenshot with optional element highlighting
        
        Args:
            page: Playwright page object
            step_name: Name of the current step
            selector: Optional CSS selector to highlight
            description: Description of what's being highlighted
        """
        try:
            # Highlight element if selector provided
            if selector:
                await page.evaluate('''(selector) => {
                    const element = document.querySelector(selector);
                    if (element) {
                        element.style.border = '3px solid red';
                        element.style.backgroundColor = 'rgba(255, 0, 0, 0.1)';
                        element.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }
                }''', selector)
                
                # Wait for scroll
                await page.wait_for_timeout(500)
            
            # Take screenshot
            result = await self.capture(page, step_name, description)
            
            # Remove highlight
            if selector:
                await page.evaluate('''(selector) => {
                    const element = document.querySelector(selector);
                    if (element) {
                        element.style.border = '';
                        element.style.backgroundColor = '';
                    }
                }''', selector)
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to capture with highlight: {e}")
            return None
    
    async def create_summary(self) -> str:
        """Create a summary page with all screenshots"""
        try:
            timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
            
            # List all screenshots for this job
            prefix = f"debug/jobs/{self.job_id}/screenshots/"
            blobs = list(self.bucket.list_blobs(prefix=prefix))
            
            screenshots = []
            for blob in blobs:
                if blob.name.endswith('.png'):
                    signed_url = blob.generate_signed_url(
                        version="v4",
                        expiration=3600,
                        method="GET"
                    )
                    screenshots.append({
                        'name': blob.name.split('/')[-1],
                        'url': signed_url,
                        'path': f"gs://{self.bucket_name}/{blob.name}"
                    })
            
            # Create HTML summary
            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Job Debug: {self.job_id}</title>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 20px; }}
                    .screenshot {{ margin: 20px 0; border: 1px solid #ccc; padding: 10px; }}
                    img {{ max-width: 100%; height: auto; }}
                    .info {{ background: #f0f0f0; padding: 10px; margin: 10px 0; }}
                </style>
            </head>
            <body>
                <h1>Debug Screenshots for Job: {self.job_id}</h1>
                <p>Generated at: {timestamp}</p>
                <p>Total screenshots: {len(screenshots)}</p>
                
                <h2>Screenshots:</h2>
            """
            
            for i, screenshot in enumerate(screenshots):
                html += f"""
                <div class="screenshot">
                    <h3>{i+1}. {screenshot['name']}</h3>
                    <p>Path: {screenshot['path']}</p>
                    <img src="{screenshot['url']}" alt="{screenshot['name']}">
                </div>
                """
            
            html += """
            </body>
            </html>
            """
            
            # Upload summary
            summary_path = f"debug/jobs/{self.job_id}/summary.html"
            summary_blob = self.bucket.blob(summary_path)
            summary_blob.upload_from_string(html, content_type='text/html')
            
            # Get signed URL
            summary_url = summary_blob.generate_signed_url(
                version="v4",
                expiration=3600,
                method="GET"
            )
            
            logger.info(f"Debug summary created: {summary_url}")
            return summary_url
            
        except Exception as e:
            logger.error(f"Failed to create summary: {e}")
            return ""
    
    async def test_screenshot(self) -> bool:
        """Test if screenshots are working"""
        logger.info(f"[ScreenshotDebugger] Testing screenshot capability")
        try:
            from jobber_fsm.core.web_driver.playwright import PlaywrightManager
            browser_manager = PlaywrightManager()
            page = await browser_manager.get_current_page()
            
            if not page:
                logger.error("[ScreenshotDebugger] No page available for test")
                return False
                
            # Try to take a test screenshot
            path = await self.capture(page, "test_screenshot", "Testing screenshot capability")
            if path:
                logger.info(f"[ScreenshotDebugger] Test successful: {path}")
                return True
            else:
                logger.error("[ScreenshotDebugger] Test failed")
                return False
        except Exception as e:
            logger.error(f"[ScreenshotDebugger] Test error: {e}", exc_info=True)
            return False
```

# jobber_fsm/utils/ui_messagetype.py

```py
from enum import Enum


class MessageType(Enum):
    PLAN = "plan"
    STEP = "step"
    ACTION = "action"
    ANSWER = "answer"
    QUESTION = "question"
    INFO = "info"
    FINAL = "final"
    DONE = "transaction_done"
    ERROR = "error"

```

# logs.txt

```txt
Starting System Orchestrator...
Browser profile /Users/namanjain/Library/Application Support/Google/Chrome
Browser started and ready.
Enter your command (or type 'exit' to quit): 
```

# profile.json

```json
{
  "first_name": "Alice",
  "last_name": "Ng",
  "email": "alice@example.com",
  "phone": "+1-555-123-4567",
  "linkedin_profile": "https://linkedin.com/in/aliceng",
  "date_of_birth": "1997-04-03",
  "gender": "Female",
  "race_ethnicity": "Asian",
  "veteran_status": "No",
  "disability_status": "No",
  "visa_countries": [
    {"code": "US", "name": "United States", "requiresSponsorship": false}
  ],
  "resume_path": "/absolute/path/AliceNgResume.pdf"
}

```

# README.md

```md
# sentient

this agent is based on our upcoming open-source framework [sentient](https://github.com/sentient-engineering/sentient) to help devs instantly build fast & reliable AI agents that can control browsers autonomously in 3 lines of code. checkout the beta sentient package on [pypi](https://pypi.org/project/sentient/) & our experiemnts to advance oss web navigating agents in the [agent-q repository](https://github.com/sentient-engineering/agent-q)

# jobber - apply to relevant jobs on internet autonomously

jobber is an ai agent that searches and applies for jobs on your behalf by controlling your browser. put in your resume and preferences and it does the work in background.

### demo

checkout this [loom video](https://www.loom.com/share/2037ee751b4f491c8d2ffd472d8223bd?sid=53d08a9f-5a9b-4388-ae69-445032b31738) for a quick demo

### jobber and jobber_fsm

you might notice two separate implementations of jobber in the repo. `jobber` folder contains a simpler approach to implementing multi-agent conversation required between a planner and a browser agent.

the `jobber_fsm` folder contains another approach based on [finite state machines](https://github.com/sentient-engineering/multi-agent-fsm). there are slight nuances and both result in similar level or performace. however, the fsm approach is more scalable, and we will be doing further improvements in it.

the downside of fsm agent is that it is dependent on [structured output](https://openai.com/index/introducing-structured-outputs-in-the-api/) from open ai. so you can't reliably use cheaper models like gpt4o-mini or other oss models which is possible in `jobber`

### setup

1. we recommend installing poetry before proceeding with the next steps. you can install poetry using these [instructions](https://python-poetry.org/docs/#installation)

2. install dependencies

\`\`\`bash
poetry install
\`\`\`

3. start chrome in dev mode - in a seaparate terminal, use the command to start a chrome instance and do necesssary logins to job websites like linkedin/ wellfound, etc.

for mac, use command -

\`\`\`bash
sudo /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
\`\`\`

for linux -

\`\`\`bash
google-chrome --remote-debugging-port=9222
\`\`\`

for windows -

\`\`\`bash
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
\`\`\`

4. set up env - add openai and [langsmith](https://smith.langchain.com) keys to .env file. you can refer .env.example. currently adding langsmith is required but if you do not want to use it for tracing - then you can comment the line `litellm.success_callback = ["langsmith"]` in the `./jobber_fsm/core/agent/base.py` file.

5. update your preferences in the `user_preferences.txt` file in the folder of agent that you are running (jobber/ jobber_fsm). provide the local file path to your resume in this file itself for the agent to be able to upload it.

6. run the agent - jobber_fsm or jobber

\`\`\`bash
python -u -m jobber_fsm
\`\`\`

or

\`\`\`bash
python -u -m jobber
\`\`\`

6. enter your task. sample task -

\`\`\`bash
apply for a backend engineer role based in helsinki on linkedin
\`\`\`

### Run evals

1. For Jobber

\`\`\`bash
 python -m test.tests_processor --orchestrator_type vanilla
\`\`\`

2. For Jobber FSM

\`\`\`bash
 python -m test.tests_processor --orchestrator_type fsm
\`\`\`

#### citations

a bunch of amazing work in the space has inspired this. see [webvoyager](https://arxiv.org/abs/2401.13919), [agent-e](https://arxiv.org/abs/2407.13032)

\`\`\`
@article{he2024webvoyager,
  title={WebVoyager: Building an End-to-End Web Agent with Large Multimodal Models},
  author={He, Hongliang and Yao, Wenlin and Ma, Kaixin and Yu, Wenhao and Dai, Yong and Zhang, Hongming and Lan, Zhenzhong and Yu, Dong},
  journal={arXiv preprint arXiv:2401.13919},
  year={2024}
}
\`\`\`

\`\`\`
@misc{abuelsaad2024-agente,
      title={Agent-E: From Autonomous Web Navigation to Foundational Design Principles in Agentic Systems},
      author={Tamer Abuelsaad and Deepak Akkil and Prasenjit Dey and Ashish Jagmohan and Aditya Vempaty and Ravi Kokku},
      year={2024},
      eprint={2407.13032},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2407.13032},
}
\`\`\`

```

