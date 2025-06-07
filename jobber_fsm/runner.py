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