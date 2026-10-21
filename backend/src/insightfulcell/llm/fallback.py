"""Main model first, a backup model when it fails (the engine's ``LanguageModel`` port).

Each model is a full ``LLMGateway`` with its own breaker, quota pause and pacing, so a main
provider that is out of quota or failing is skipped quickly (its breaker or pause answers at
once) while the backup serves. Both share the ledger and the spend cap.

The backup is not tried when trying it cannot help or must not happen: the privacy check
blocked the request (that data must not go to a second provider either), the overall or the
person's spend cap has no room, or the run's or organisation's token budget is spent. A main
provider at its own cap (``spend_cap_provider``) is the backup's turn: its cap is separate.
When both fail, the caller falls back to the rules, as with one model.
"""

from __future__ import annotations

import logging
from typing import Final

from insightfulcell.engine.ports import ModelReply, ModelRequest, ModelUnavailable
from insightfulcell.llm.gateway import LLMGateway

logger = logging.getLogger(__name__)

NO_BACKUP_FOR: Final = frozenset(
    {
        "payload_blocked",
        "spend_cap",
        "spend_cap_user",
        "spend_unavailable",
        "budget_run",
        "budget_org",
    }
)


class FallbackModel:
    def __init__(self, primary: LLMGateway, backup: LLMGateway | None) -> None:
        self.primary = primary
        self.backup = backup if backup is not None and backup.identity is not None else None

    @property
    def identity(self) -> tuple[str, str] | None:
        return self.primary.identity or (self.backup.identity if self.backup else None)

    async def complete(self, request: ModelRequest) -> ModelReply:
        try:
            return await self.primary.complete(request)
        except ModelUnavailable as exc:
            if self.backup is None or exc.reason in NO_BACKUP_FOR:
                raise
            logger.warning(
                "main language model unavailable: asking the backup",
                extra={
                    "reason": exc.reason,
                    "purpose": request.purpose.value,
                    "backup": self.backup.identity,
                },
            )
            return await self.backup.complete(request)
