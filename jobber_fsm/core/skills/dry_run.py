# jobber_fsm/core/skills/dry_run.py
"""
Decorator that turns every Playwright-touching skill into a NO-OP when the
process is launched with  --dry-run  (or the env-var DRY_RUN=1).

Usage
-----
from .dry_run import maybe_skip_action

@maybe_skip_action
async def openurl(...):
    ...

A skipped call just logs the intent and returns None.
"""
import functools, os, logging, inspect, asyncio

_LOG = logging.getLogger("jobber.dryrun")
_DRY = os.getenv("DRY_RUN", "").lower() in {"1", "true", "yes"}

def maybe_skip_action(fn):
    is_async = inspect.iscoroutinefunction(fn)

    @functools.wraps(fn)
    async def _async_wrapper(*args, **kwargs):
        if _DRY:
            _LOG.info("[dry-run] would call  %s%r", fn.__name__, args or kwargs)
            return None
        return await fn(*args, **kwargs)

    @functools.wraps(fn)
    def _sync_wrapper(*args, **kwargs):
        if _DRY:
            _LOG.info("[dry-run] would call  %s%r", fn.__name__, args or kwargs)
            return None
        return fn(*args, **kwargs)

    return _async_wrapper if is_async else _sync_wrapper
