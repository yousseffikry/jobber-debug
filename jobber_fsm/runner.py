# jobber_fsm/runner.py
"""
Programmatic entry-point for autonomous job applications.

Usage
-----
from jobber_fsm.runner import apply_for_job

result: dict = await apply_for_job(
        url="https://jobs.acme.com/apply?id=123",
        profile={
            "first_name": "Ava",
            "last_name": "Chen",
            "email": "ava@example.com",
            "phone": "555-1234",
            "password": "TempPass!23",
            "visa_status": "US Citizen",
            "gender": "Female",
            "race_ethnicity": "Asian",
            "veteran_status": "No",
            "disability_status": "No",
            "date_of_birth": "1995-04-12",
            "linkedin_profile": "https://linkedin.com/in/ava-chen",
        },
        resume_path="/tmp/ava_cv.pdf",
        headless=True,          # cloud friendly
        eval_mode=False,        # Set True if you want an isolated temp profile
)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Any

from jobber_fsm.core.agent.browser_nav_agent import BrowserNavAgent
from jobber_fsm.core.agent.planner_agent import PlannerAgent
from jobber_fsm.core.models.models import State
from jobber_fsm.core.orchestrator.orchestrator import Orchestrator


def _build_namespace(url: str, profile: Dict[str, Any], resume_path: str) -> SimpleNamespace:
    """
    Convert keyword arguments into the same object argparse would produce
    in the CLI version.
    """
    ns = SimpleNamespace()
    # mandatory
    ns.url = url
    ns.first_name = profile.get("first_name")
    ns.last_name = profile.get("last_name")
    ns.email = profile.get("email")
    ns.phone = profile.get("phone")
    ns.resume = Path(resume_path).expanduser().as_posix()

    # optional / extended
    ns.password = profile.get("password") or "Temp123!Jobber"
    ns.visa_status = profile.get("visa_status")
    ns.gender = profile.get("gender")
    ns.race_ethnicity = profile.get("race_ethnicity")
    ns.veteran_status = profile.get("veteran_status")
    ns.disability_status = profile.get("disability_status")
    ns.date_of_birth = profile.get("date_of_birth")
    ns.linkedin_profile = profile.get("linkedin_profile")

    # runtime flags
    ns.auto = True          # <-- skip the interactive shell
    ns.headless = True      # caller can override via kwargs
    ns.eval_mode = False

    return ns


async def _run_orchestrator(arg_ns: SimpleNamespace, headless: bool, eval_mode: bool) -> Dict[str, Any]:
    """Spin up the FSM exactly like `__main__.py`, but fully automated."""
    # Overwrite runtime toggles if caller passed them
    arg_ns.headless = headless
    arg_ns.eval_mode = eval_mode

    # Wire up the state machine
    state_to_agent_map = {
        State.PLAN: PlannerAgent(arg_ns),      # pass namespace so agent can read profile
        State.BROWSE: BrowserNavAgent(arg_ns),
    }

    orchestrator = Orchestrator(state_to_agent_map=state_to_agent_map)
    result_dict: Dict[str, Any] = await orchestrator.start_auto()   # you’ll add start_auto() below
    return result_dict


async def apply_for_job(
    url: str,
    profile: Dict[str, Any],
    resume_path: str,
    *,
    headless: bool = True,
    eval_mode: bool = False,
) -> Dict[str, Any]:
    """
    High-level coroutine that your FastAPI endpoint can `await`.

    Returns
    -------
    Dictionary with keys like
        {
          "success": True,
          "message": "Application submitted",
          "confirmation_url": "...",
          "steps": [...]
        }
    or an error description.
    """
    ns = _build_namespace(url, profile, resume_path)

    try:
        result: Dict[str, Any] = await _run_orchestrator(ns, headless, eval_mode)
        return {"success": True, **result}
    except Exception as exc:
        return {"success": False, "message": str(exc)}
