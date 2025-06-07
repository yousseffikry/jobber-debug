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