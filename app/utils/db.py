"""
PostgreSQL 연결 관리.
SQLAlchemy async 엔진 + 세션 팩토리.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text

from app.config import settings

logger = logging.getLogger(__name__)

# ── 엔진 ──
_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """async 엔진 싱글턴 반환"""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.postgres.dsn,
            pool_size=5,
            max_overflow=10,
            echo=False,
            connect_args={"timeout": 5},
        )
    return _engine


# ── 세션 팩토리 ──
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """세션 컨텍스트 매니저. 자동 커밋/롤백 처리."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── 헬스체크 ──
async def check_connection() -> bool:
    """DB 연결 상태 확인"""
    try:
        async with get_session() as session:
            result = await session.execute(text("SELECT 1"))
            return result.scalar() == 1
    except Exception as e:
        logger.error("PostgreSQL 연결 실패: %s", e)
        return False


# ── 종료 ──
async def close_engine() -> None:
    """엔진 종료 (앱 셧다운 시 호출)"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
