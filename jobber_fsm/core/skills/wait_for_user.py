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