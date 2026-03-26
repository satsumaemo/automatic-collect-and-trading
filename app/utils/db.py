"""
PostgreSQL 연결 관리.
동기 엔진(psycopg2) + async 호환 인터페이스.

외부에서는 기존과 동일하게 사용:
    async with get_session() as session:
        result = session.execute(text("SELECT ..."), params)
        rows = result.fetchall()

내부적으로 동기 psycopg2 연결을 사용하므로
session.execute()에 await를 붙이지 않습니다.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

# ── 엔진 ──
_engine: Engine | None = None


def get_engine() -> Engine:
    """동기 엔진 싱글턴 반환 (psycopg2)"""
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.postgres.sync_dsn,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=False,
            connect_args={"client_encoding": "utf8"},
        )
    return _engine


# ── 세션 팩토리 ──
_session_factory: sessionmaker | None = None


def get_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[Session, None]:
    """
    세션 컨텍스트 매니저.
    async with로 사용하되, 내부 세션은 동기(psycopg2).
    session.execute()에 await를 붙이지 않습니다.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ── 헬스체크 ──
async def check_connection() -> bool:
    """DB 연결 상태 확인"""
    try:
        async with get_session() as session:
            result = session.execute(text("SELECT 1"))
            return result.scalar() == 1
    except Exception as e:
        logger.error("PostgreSQL 연결 실패: %s", e)
        return False


# ── 종료 ──
async def close_engine() -> None:
    """엔진 종료 (앱 셧다운 시 호출)"""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
        _engine = None
        _session_factory = None
