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