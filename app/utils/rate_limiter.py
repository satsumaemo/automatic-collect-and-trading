"""
토큰 버킷 레이트 리미터.
우선순위 큐를 지원하는 async 레이트 리미터입니다.

우선순위:
  1 = 주문 조회 (최우선)
  2 = 실시간 시세
  3 = 일봉 수집
  4 = 백필
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

# 우선순위 상수
PRIORITY_ORDER_CHECK = 1
PRIORITY_REALTIME = 2
PRIORITY_DAILY = 3
PRIORITY_BACKFILL = 4


class RateLimitError(Exception):
    """레이트 리밋 초과 시 발생"""
    pass


@dataclass
class _WaitEntry:
    """우선순위 큐 항목"""
    priority: int
    created_at: float = field(default_factory=time.monotonic)
    event: asyncio.Event = field(default_factory=asyncio.Event)

    def __lt__(self, other: "_WaitEntry") -> bool:
        # 우선순위 높을수록(낮은 숫자) 먼저, 같으면 먼저 등록된 것
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.created_at < other.created_at


class TokenBucketRateLimiter:
    """토큰 버킷 방식 레이트 리미터"""

    def __init__(self, rate: Optional[int] = None) -> None:
        self._rate = rate or settings.kis.rate_limit  # 초당 허용 수
        self._tokens = float(self._rate)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._waiters: list[_WaitEntry] = []

    def _refill(self) -> None:
        """시간 경과에 따라 토큰 보충"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
        self._last_refill = now

    async def acquire(self, priority: int = PRIORITY_DAILY) -> None:
        """
        토큰 1개 획득. 토큰이 없으면 대기.
        우선순위가 높은 요청이 먼저 깨어남.
        """
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return

            # 토큰 부족 → 대기 등록
            entry = _WaitEntry(priority=priority)
            self._waiters.append(entry)
            self._waiters.sort()

            # 50ms 간격 체크
            await asyncio.sleep(0.05)

            # 재시도
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0 and self._waiters and self._waiters[0] is entry:
                    self._tokens -= 1.0
                    self._waiters.remove(entry)
                    return
                elif entry in self._waiters:
                    self._waiters.remove(entry)

    async def acquire_with_retry(
        self,
        priority: int = PRIORITY_DAILY,
        max_retries: int = 3,
    ) -> None:
        """레이트 리밋 에러 시 1초 대기 후 재시도"""
        for attempt in range(max_retries):
            try:
                await self.acquire(priority)
                return
            except RateLimitError:
                if attempt < max_retries - 1:
                    logger.warning("레이트 리밋 초과, 1초 대기 후 재시도 (%d/%d)", attempt + 1, max_retries)
                    await asyncio.sleep(1.0)
                else:
                    raise

    @property
    def available_tokens(self) -> float:
        self._refill()
        return self._tokens


# 전역 리미터 (모듈 로드 시 생성)
rate_limiter = TokenBucketRateLimiter()
