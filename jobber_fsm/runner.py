"""
Un-attended application runner for Jobber-FSM.

Example:
    poetry run jobber-apply \\
        --url https://boards.greenhouse.io/openai/jobs/1234567 \\
        --profile ./profile.json \\
        --headless --dry-run
"""
from __future__ import annotations
import argparse, asyncio, json, pathlib, sys

from jobber_fsm.core.agent.browser_nav_agent import BrowserNavAgent
from jobber_fsm.core.agent.planner_agent     import PlannerAgent
from jobber_fsm.core.models.models           import State
from jobber_fsm.core.orchestrator.orchestrator import Orchestrator
from jobber_fsm.core.memory import ltm
from jobber_fsm.core.models.models import PlannerInput


def _cli() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--url",     required=True, help="Job apply link")
    p.add_argument("--profile", required=True, help="Path to JSON profile file")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--dry-run",  action="store_true")
    return p.parse_args()


async def _main() -> None:
    args = _cli()

    # ---------- load profile ----------
    profile_path = pathlib.Path(args.profile).expanduser()
    if not profile_path.is_file():
        sys.exit(f"[runner] profile file not found: {profile_path}")
    with profile_path.open() as f:
        profile = json.load(f)

    # ---------- stash data in memory for agents ----------
    ltm.set_job_apply_context(url=args.url, profile=profile)

    # ---------- build agents ----------
    planner  = PlannerAgent(auto_mode=True)
    executor = BrowserNavAgent(planner, auto_mode=True)

    state_map = {
        State.PLAN:   planner,
        State.BROWSE: executor,
    }

    orch = Orchestrator(
        state_to_agent_map=state_map,
        # eval_mode=True,   # optional temp-profile browser
    )
    await orch._bootstrap()
    await planner.process_query("start")


def entrypoint() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    asyncio.run(_main())
