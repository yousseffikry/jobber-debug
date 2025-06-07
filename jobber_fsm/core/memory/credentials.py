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