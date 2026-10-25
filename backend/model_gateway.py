"""ACP model gateway. Execution policies are injected; no task/database imports."""

from typing import Protocol
from . import acp_client


class CallContext(Protocol):
    def reserve(self): ...
    def cancelled(self) -> bool: ...
    def record_usage(self, tokens: int): ...


def complete(prompt: str, model: str, context: CallContext) -> str:
    context.reserve()
    result = acp_client.prompt(prompt, model=model, cancel_check=context.cancelled)
    context.record_usage(result.usage)
    return result.text
