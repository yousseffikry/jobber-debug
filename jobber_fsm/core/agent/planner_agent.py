# jobber_fsm/core/agent/planner_agent.py
from __future__ import annotations

from datetime import datetime
from string import Template
from typing import Any

from pydantic import BaseModel

from jobber_fsm.core.agent.base import BaseAgent
from jobber_fsm.core.agent.browser_nav_agent import BrowserNavAgent
from jobber_fsm.core.memory import ltm
from jobber_fsm.core.prompts import LLM_PROMPTS
from jobber_fsm.core.skills.get_screenshot import get_screenshot


# --------------------------------------------------------------------------- #
# Pydantic I/O schemas expected by BaseAgent
# --------------------------------------------------------------------------- #
class PlannerInput(BaseModel):
    query: str


class PlannerOutput(BaseModel):
    terminate: bool
    content: str


# --------------------------------------------------------------------------- #
# The planner
# --------------------------------------------------------------------------- #
class PlannerAgent(BaseAgent):
    """
    High-level planner.

    auto_mode = True  ➜ run fully autonomously (no interactive questions)  
    auto_mode = False ➜ fallback to asking the human when needed
    """

    def __init__(self, *, auto_mode: bool = False) -> None:
        self.auto_mode = auto_mode

        # ── Build the system prompt ────────────────────────────────────────────
        user_ltm = "\n" + (ltm.get_user_ltm() or "")
        system_prompt = Template(
            LLM_PROMPTS["PLANNER_AGENT_PROMPT"]
        ).substitute(basic_user_information=user_ltm)

        today = datetime.now()
        system_prompt += f"\nToday's date is: {today:%d/%m/%Y}"
        system_prompt += f"\nCurrent weekday is: {today:%A}"

        # ── Initialise BaseAgent ───────────────────────────────────────────────
        super().__init__(
            name="planner",
            system_prompt=system_prompt,
            input_format=PlannerInput,
            output_format=PlannerOutput,
            keep_message_history=auto_mode,
        )

        # The browser-level executor
        self.browser_agent = BrowserNavAgent(self, auto_mode=auto_mode)

    # --------------------------------------------------------------------- #
    # public API
    # --------------------------------------------------------------------- #
    async def process_query(self, query: str) -> Any:
        """
        Receive an instruction from the orchestrator, produce / refine a plan,
        hand sub-tasks to the BrowserNavAgent, loop until `terminate == True`.
        """
        response: PlannerOutput = await self.run(PlannerInput(query=query))

        while True:
            if response.terminate:
                return response.content

            browser_response: PlannerOutput = await self.browser_agent.process_query(
                response.content
            )

            if browser_response.terminate:
                return browser_response.content

            response = browser_response  # continue the loop

    # Called by BrowserNavAgent when it has a message + screenshot for us
    async def receive_browser_message(self, message: str):
        screenshot = await get_screenshot()

        return await self.generate_reply(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Helper response: {message}\n"
                                "Here is a screenshot of the current browser page"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": screenshot},
                        },
                    ],
                }
            ],
            self.browser_agent,
        )
