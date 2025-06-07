"""
Cloud runner for Jobber FSM - Production ready for mobile app integration
"""
import os
import logging
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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Ensure logger outputs to stdout
logger.handlers = []
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.setLevel(logging.INFO)

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
    
    # Get job details from environment
    job_data_str = os.environ.get('JOB_DATA', '{}')
    job_id = os.environ.get('JOB_ID', str(int(time.time())))
    
    # Add these debug lines
    print(f"[cloud_job_runner] Raw JOB_DATA: {job_data_str[:200]}...", flush=True)
    print(f"[cloud_job_runner] JOB_ID: {job_id}", flush=True)
    print(f"[cloud_job_runner] JOB_DATA length: {len(job_data_str)}", flush=True)
    
    logger.info(f"Starting job {job_id}")
    logger.info(f"Raw JOB_DATA: {job_data_str}")
    
    try:
        job_data = json.loads(job_data_str)
        print(f"[cloud_job_runner] Successfully parsed job data", flush=True)
        print(f"[cloud_job_runner] Job data keys: {list(job_data.keys())}", flush=True)
    except json.JSONDecodeError as e:
        print(f"[cloud_job_runner] ERROR parsing JOB_DATA: {e}", flush=True)
        logger.error(f"Failed to parse JOB_DATA: {e}")
        await store_result('unknown', 'unknown', job_id, "failed", f"Invalid JOB_DATA format: {e}")
        return
    
    # Extract required fields
    user_id = job_data.get('userId')
    job_url = job_data.get('jobUrl')
    resume_gcs_path = job_data.get('resumeGcsPath')
    user_profile = job_data.get('userProfile', {})
    
    print(f"[cloud_job_runner] Extracted data:", flush=True)
    print(f"  user_id: {user_id}", flush=True)
    print(f"  job_url: {job_url}", flush=True)
    print(f"  resume_gcs_path: {resume_gcs_path}", flush=True)
    print(f"  user_profile keys: {list(user_profile.keys()) if user_profile else 'None'}", flush=True)
    
    if not all([user_id, job_url, resume_gcs_path]):
        error_msg = f"Missing required fields. userId: {user_id}, jobUrl: {job_url}, resumeGcsPath: {resume_gcs_path}"
        print(f"[cloud_job_runner] ERROR: {error_msg}", flush=True)
        logger.error(error_msg)
        await store_result(user_id or 'unknown', job_url or 'unknown', job_id, "failed", error_msg)
        return
    
    print(f"[cloud_job_runner] All required fields present, continuing...", flush=True)
    
    try:
        # Download resume from GCS
        print(f"[cloud_job_runner] About to download resume from: {resume_gcs_path}", flush=True)
        logger.info("Downloading resume from GCS...")
        local_resume_path = await download_resume(resume_gcs_path)
        print(f"[cloud_job_runner] Resume downloaded successfully to: {local_resume_path}", flush=True)
        logger.info(f"Resume downloaded to: {local_resume_path}")
        
        # Build complete profile for the AI agent
        print(f"[cloud_job_runner] Building complete profile...", flush=True)
        logger.info("Building complete profile...")
        complete_profile = build_complete_profile(user_profile, local_resume_path)
        print(f"[cloud_job_runner] Profile built successfully", flush=True)
        
        logger.info(f"Complete profile prepared: {json.dumps(complete_profile, indent=2)}")
        
        # Set up job context for the AI agent
        print(f"[cloud_job_runner] Setting up job context in LTM...", flush=True)
        logger.info("Setting up job context in LTM...")
        ltm.set_job_apply_context(url=job_url, profile=complete_profile)
        print(f"[cloud_job_runner] Job context set successfully", flush=True)
        
        # Initialize screenshot debugger
        print(f"[cloud_job_runner] Initializing screenshot debugger...", flush=True)
        logger.info("Initializing screenshot debugger...")
        screenshot_debugger = ScreenshotDebugger(job_id)
        print(f"[cloud_job_runner] Screenshot debugger initialized", flush=True)

        # Test screenshot capability
        print(f"[cloud_job_runner] Testing screenshot capability...", flush=True)
        logger.info("Testing screenshot capability...")
        test_result = await screenshot_debugger.test_screenshot()
        print(f"[cloud_job_runner] Screenshot test result: {test_result}", flush=True)
        logger.info(f"Screenshot test result: {test_result}")
        
        # Create agents
        print(f"[cloud_job_runner] Creating AI agents...", flush=True)
        logger.info("Creating AI agents...")
        planner = PlannerAgent(auto_mode=True)
        print(f"[cloud_job_runner] PlannerAgent created", flush=True)
        logger.info("  PlannerAgent created")

        # Inject screenshot debugger into the planner's browser agent
        print(f"[cloud_job_runner] Injecting screenshot debugger...", flush=True)
        if hasattr(planner, 'browser_agent') and planner.browser_agent:
            planner.browser_agent.screenshot_debugger = screenshot_debugger
            print(f"[cloud_job_runner] Screenshot debugger injected successfully", flush=True)
            logger.info("  Screenshot debugger injected into planner's browser agent")
        else:
            print(f"[cloud_job_runner] ERROR: Planner has no browser_agent!", flush=True)
            logger.error("  ERROR: Planner does not have browser_agent attribute!")
        
        state_map = {
            State.PLAN: planner,
            State.BROWSE: planner.browser_agent,
        }
        
        # Initialize orchestrator
        print(f"[cloud_job_runner] Creating orchestrator...", flush=True)
        logger.info("Initializing orchestrator...")
        orch = Orchestrator(state_to_agent_map=state_map)
        print(f"[cloud_job_runner] About to bootstrap orchestrator...", flush=True)
        await orch._bootstrap()
        print(f"[cloud_job_runner] Orchestrator bootstrap completed", flush=True)
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
        print(f"[cloud_job_runner] EXCEPTION in main try block: {type(e).__name__}: {str(e)}", flush=True)
        logger.error(f"[cloud_job_runner] Exception details:", exc_info=True)
        import traceback
        traceback.print_exc()
        error_msg = f"Application failed: {str(e)}"
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