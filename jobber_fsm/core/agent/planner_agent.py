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