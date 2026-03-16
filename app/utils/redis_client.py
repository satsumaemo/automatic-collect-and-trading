"""
Redis 연결 + 헬퍼 함수.
시세, 시스템 상태, 포트폴리오 캐시를 관리합니다.

키 패턴:
  market:price:{ticker}   → {"price", "volume", "ts"}
  portfolio:positions      → {ticker: {qty, avg_price, pnl}}
  portfolio:cash           → {"krw": ..., "usd": ...}
  system:alert_level       → "normal"
  system:market_regime     → "expansion"
  system:last_heartbeat    → timestamp
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

# ── 연결 ──
_redis: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    """Redis 연결 싱글턴 반환"""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis.url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
    return _redis


async def close_redis() -> None:
    """Redis 연결 종료"""
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None


# ── JSON 헬퍼 ──
async def set_json(key: str, data: dict, expire: Optional[int] = None) -> None:
    """dict를 JSON 문자열로 저장"""
    r = await get_redis()
    await r.set(key, json.dumps(data, default=str), ex=expire)


async def get_json(key: str) -> Optional[dict]:
    """JSON 문자열을 dict로 반환"""
    r = await get_redis()
    raw = await r.get(key)
    if raw is None:
        return None
    return json.loads(raw)


# ── 시세 ──
async def set_price(ticker: str, price: float, volume: int) -> None:
    """실시간 시세 저장 (60초 만료)"""
    await set_json(
        f"market:price:{ticker}",
        {"price": price, "volume": volume, "ts": datetime.now().isoformat()},
        expire=60,
    )


async def get_price(ticker: str) -> Optional[dict]:
    """실시간 시세 조회"""
    return await get_json(f"market:price:{ticker}")


# ── 시스템 상태 ──
async def set_alert_level(level: str) -> None:
    r = await get_redis()
    await r.set("system:alert_level", level)


async def get_alert_level() -> str:
    r = await get_redis()
    val = await r.get("system:alert_level")
    return val or "normal"


async def set_regime(regime: str) -> None:
    r = await get_redis()
    await r.set("system:market_regime", regime)


async def get_regime() -> str:
    r = await get_redis()
    val = await r.get("system:market_regime")
    return val or "expansion"


async def set_heartbeat() -> None:
    r = await get_redis()
    await r.set("system:last_heartbeat", datetime.now().isoformat())


# ── 포트폴리오 ──
async def set_positions(positions: Dict[str, dict]) -> None:
    """포지션 정보 저장"""
    await set_json("portfolio:positions", positions)


async def get_positions() -> Dict[str, dict]:
    """포지션 정보 조회"""
    data = await get_json("portfolio:positions")
    return data or {}


async def set_cash(krw: float, usd: float = 0.0) -> None:
    await set_json("portfolio:cash", {"krw": krw, "usd": usd})


async def get_cash() -> dict:
    data = await get_json("portfolio:cash")
    return data or {"krw": 0, "usd": 0}


# ── 헬스체크 ──
async def check_connection() -> bool:
    """Redis 연결 상태 확인"""
    try:
        r = await get_redis()
        return await r.ping()
    except Exception as e:
        logger.error("Redis 연결 실패: %s", e)
        return False
