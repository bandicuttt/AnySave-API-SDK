# anysave_sdk/_concurrency.py

from __future__ import annotations

import asyncio
import logging

from contextlib import asynccontextmanager
from typing import AsyncIterator

from anysave_sdk.exceptions import ConcurrencyLimitTimeoutError

logger = logging.getLogger("api_client")


class LocalProcessingLimiter:
    """
    Глобальный лимитер локальной client-side обработки.

    Ограничивает число одновременно выполняемых local /links задач.
    Если слот не освободился за wait_timeout_s — бросает
    ConcurrencyLimitTimeoutError.
    """

    def __init__(
        self,
        *,
        max_concurrency: int,
        wait_timeout_s: float,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if wait_timeout_s <= 0:
            raise ValueError("wait_timeout_s must be > 0")

        self.max_concurrency = int(max_concurrency)
        self.wait_timeout_s = float(wait_timeout_s)
        self._semaphore = asyncio.BoundedSemaphore(self.max_concurrency)

    @asynccontextmanager
    async def slot(self, label: str = "local_processing") -> AsyncIterator[None]:
        acquired = False
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self.wait_timeout_s,
            )
            acquired = True

            logger.debug(
                "Local processing slot acquired | label=%s max_concurrency=%d",
                label,
                self.max_concurrency,
            )
            yield

        except asyncio.TimeoutError as exc:
            raise ConcurrencyLimitTimeoutError(
                label=label,
                max_concurrency=self.max_concurrency,
                timeout_s=self.wait_timeout_s,
            ) from exc

        finally:
            if acquired:
                self._semaphore.release()
                logger.debug("Local processing slot released | label=%s", label)