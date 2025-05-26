from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from string import Template

from jobber_fsm.core.agent.base import BaseAgent
from jobber_fsm.core.agent.browser_nav_agent import BrowserNavAgent
from jobber_fsm.core.memory import ltm
from jobber_fsm.core.prompts import LLM_PROMPTS
from jobber_fsm.core.skills.get_screenshot import get_screenshot


class PlannerAgent(BaseAgent):
    """
    High-level agent that decomposes the goal (“apply to this job”) into steps
    BrowserNavAgent can execute.

    It now **receives a SimpleNamespace (`args`)** that contains every user-profile
    field and the target `url`, supplied by `jobber_fsm.runner.apply_for_job()`.
    """

    # --------------------------------------------------------------------- #
    #   Construction                                                        #
    # --------------------------------------------------------------------- #
    def __init__(self, args_namespace: SimpleNamespace | None = None) -> None:
        if args_namespace is None:
            raise RuntimeError(
                "PlannerAgent now expects the args namespace with user profile data"
            )

        self.args = args_namespace  # store for later use inside prompts / tools

        # ------------ Build the system-prompt dynamically ---------------- #
        # 1. The original “planner” prompt template from prompts.py
        system_prompt: str = LLM_PROMPTS["PLANNER_AGENT_PROMPT"]

        # 2. Assemble long-term memory + **explicit user profile**
        user_ltm = self._build_user_ltm()  # personalised LTM
        system_prompt = Template(system_prompt).substitute(basic_user_information=user_ltm)

        # 3. Append date context (helps the model generate fresh answers)
        today = datetime.utcnow()
        system_prompt += (
            f"\nToday's date is: {today:%d/%m/%Y}"
            f"\nCurrent weekday is: {today:%A}"
        )

        # 4. Init BaseAgent
        super().__init__(system_prompt=system_prompt)
        self.browser_agent = BrowserNavAgent(self, args_namespace)

    # --------------------------------------------------------------------- #
    #   Core loop – unchanged                                               #
    # --------------------------------------------------------------------- #
    async def process_query(self, query: str):
        response = await super().process_query(query)

        while True:
            if response.get("terminate", False):
                return response["content"]

            processed = await self.browser_agent.process_query(response["content"])

            if processed.get("terminate", False):
                return processed["content"]

            response = processed

    async def receive_browser_message(self, message: str):
        screenshot = await get_screenshot()
        reply = await self.generate_reply(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Helper response: {message}\nHere is a screenshot of the current browser page",
                        },
                        {"type": "image_url", "image_url": {"url": screenshot}},
                    ],
                }
            ],
            self.browser_agent,
        )
        return reply

    # --------------------------------------------------------------------- #
    #   Helper to build personalised LTM                                    #
    # --------------------------------------------------------------------- #
    def _build_user_ltm(self) -> str:
        """
        Combine existing long-term memory with the **profile fields** passed in.
        The LLM can now reference these directly (“my email is …”).
        """
        base_ltm = ltm.get_user_ltm().strip()

        profile_lines = [
            f"First name: {self.args.first_name}",
            f"Last name: {self.args.last_name}",
            f"Email: {self.args.email}",
            f"Phone: {self.args.phone}",
            f"LinkedIn: {self.args.linkedin_profile}",
            f"Visa status: {self.args.visa_status}",
            f"Gender: {self.args.gender}",
            f"Race / Ethnicity: {self.args.race_ethnicity}",
            f"Veteran: {self.args.veteran_status}",
            f"Disability: {self.args.disability_status}",
            f"Date of birth: {self.args.date_of_birth}",
        ]
        profile_block = "\n".join([line for line in profile_lines if line.split(': ')[1]])

        return f"{base_ltm}\n{profile_block}".strip()
