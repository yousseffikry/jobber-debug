from datetime import datetime
from string import Template
from typing import Any

from jobber_fsm.core.agent.base import BaseAgent
from jobber_fsm.core.agent.browser_nav_agent import BrowserNavAgent
from jobber_fsm.core.memory import ltm
from jobber_fsm.core.prompts import LLM_PROMPTS
from jobber_fsm.core.skills.get_screenshot import get_screenshot


class PlannerAgent(BaseAgent):
    """
    High-level planner.  
    `auto_mode=True`  → never ask the human, rely solely on the LLM + tools.  
    `auto_mode=False` → fall back to interactive questions when needed.
    """

    def __init__(self, *, auto_mode: bool = False) -> None:
        self.auto_mode = auto_mode

        user_ltm = "\n" + (ltm.get_user_ltm() or "")
        system_prompt: str = Template(LLM_PROMPTS["PLANNER_AGENT_PROMPT"]).substitute(
            basic_user_information=user_ltm
        )

        # add today’s date / weekday
        today = datetime.now()
        system_prompt += f"\nToday's date is: {today:%d/%m/%Y}"
        system_prompt += f"\nCurrent weekday is: {today:%A}"

        super().__init__(system_prompt=system_prompt)

        # pass the flag down so BrowserNavAgent also knows whether it may prompt
        self.browser_agent = BrowserNavAgent(self, auto_mode=auto_mode)

    # --------------------------------------------------------------------- #
    # no changes required below this line
    # --------------------------------------------------------------------- #

    async def process_query(self, query: str) -> Any:
        response = await super().process_query(query)
        while True:
            if response.get("terminate", False):
                return response["content"]

            browser_response = await self.browser_agent.process_query(
                response["content"]
            )

            if browser_response.get("terminate", False):
                return browser_response["content"]

            response = browser_response  # loop again

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
