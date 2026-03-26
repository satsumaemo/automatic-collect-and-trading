"""
매크로 지표 수집기.
Yahoo Finance + FRED API로 글로벌 경제 지표를 수집합니다.

yfinance는 동기 라이브러리 → asyncio.to_thread()로 감싸서 사용.
FRED API 키가 없으면 FRED 스킵.
"""

import asyncio
import logging
from datetime import datetime, date, timedelta
from typing import Any, Dict, Optional

import httpx

from sqlalchemy import text

from app.config import settings
from app.models.contracts import MacroSnapshot
from app.utils.db import get_session

logger = logging.getLogger(__name__)

# ── 수집 지표 소스 매핑 ──
MACRO_SOURCES: Dict[str, dict] = {
    # Yahoo Finance
    "vix": {"source": "yahoo", "ticker": "^VIX"},
    "sp500": {"source": "yahoo", "ticker": "^GSPC"},
    "nasdaq": {"source": "yahoo", "ticker": "^IXIC"},
    "gold": {"source": "yahoo", "ticker": "GC=F"},
    "wti": {"source": "yahoo", "ticker": "CL=F"},
    "usdkrw": {"source": "yahoo", "ticker": "KRW=X"},
    # FRED API
    "us10y": {"source": "fred", "series_id": "DGS10"},
    "us2y": {"source": "fred", "series_id": "DGS2"},
    "fed_rate": {"source": "fred", "series_id": "FEDFUNDS"},
    "hy_spread": {"source": "fred", "series_id": "BAMLH0A0HYM2"},
    "cpi": {"source": "fred", "series_id": "CPIAUCSL"},
    "fsi": {"source": "fred", "series_id": "STLFSI4"},
}

# Yahoo에서도 가져올 수 있는 FRED 지표 대체
FRED_YAHOO_FALLBACK: Dict[str, str] = {
    "us10y": "^TNX",    # 10년 국채 수익률 (CBOE)
    # us2y: FRED 전용 — Yahoo 대체 없음 (스킵)
}


def _fetch_yahoo(ticker: str) -> Optional[float]:
    """yfinance로 최신 가격 1건 (동기)"""
    try:
        import yfinance as yf
        import logging as _logging
        # yfinance 내부 로거 경고 억제
        _logging.getLogger("yfinance").setLevel(_logging.CRITICAL)

        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning("Yahoo Finance 수집 실패 [%s]: %s", ticker, e)
        return None


class MacroCollector:
    """글로벌 매크로 지표 수집"""

    def __init__(self) -> None:
        self._fred_api_key = settings.data_api.fred_api_key
        self._http_client = httpx.AsyncClient(timeout=30.0)
        logger.info("MacroCollector 초기화 (FRED 키: %s)", "있음" if self._fred_api_key else "없음")

    async def collect_all(self) -> None:
        """모든 매크로 지표 수집 → DB 저장"""
        today = date.today()
        collected = 0

        for name, source_cfg in MACRO_SOURCES.items():
            try:
                value = await self._collect_one(name, source_cfg)
                if value is not None:
                    await self._save_indicator(today, name, value)
                    collected += 1
            except Exception as e:
                logger.error("매크로 지표 수집 실패 [%s]: %s", name, e)

        # yield_spread 자동 계산 (10Y - 2Y)
        try:
            us10y = await self._get_latest_value("us10y")
            us2y = await self._get_latest_value("us2y")
            if us10y is not None and us2y is not None:
                spread = us10y - us2y
                await self._save_indicator(today, "yield_spread", spread)
                collected += 1
        except Exception as e:
            logger.error("yield_spread 계산 실패: %s", e)

        logger.info("매크로 지표 수집 완료: %d건", collected)

    async def _collect_one(self, name: str, source_cfg: dict) -> Optional[float]:
        """단일 지표 수집"""
        source = source_cfg["source"]

        if source == "yahoo":
            return await asyncio.to_thread(_fetch_yahoo, source_cfg["ticker"])

        elif source == "fred":
            if not self._fred_api_key:
                # FRED 키 없으면 Yahoo 대체 시도
                fallback_ticker = FRED_YAHOO_FALLBACK.get(name)
                if fallback_ticker:
                    logger.debug("FRED 키 없음 — Yahoo 대체: %s → %s", name, fallback_ticker)
                    return await asyncio.to_thread(_fetch_yahoo, fallback_ticker)
                logger.debug("FRED 키 없음, 대체 없음 — 스킵: %s", name)
                return None
            return await self._collect_from_fred(source_cfg["series_id"])

        return None

    async def _collect_from_fred(self, series_id: str) -> Optional[float]:
        """FRED API에서 최신 값 1건"""
        try:
            resp = await self._http_client.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": self._fred_api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 1,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            observations = data.get("observations", [])
            if not observations:
                return None
            value_str = observations[0].get("value", ".")
            if value_str == ".":
                return None
            return float(value_str)
        except Exception as e:
            logger.error("FRED 수집 실패 [%s]: %s", series_id, e)
            return None

    async def _save_indicator(self, dt: date, name: str, value: float) -> None:
        """global_indicators 테이블에 저장"""
        async with get_session() as session:
            session.execute(
                text("""
                    INSERT INTO global_indicators (date, indicator_name, value)
                    VALUES (:dt, :name, :val)
                    ON CONFLICT (date, indicator_name) DO UPDATE SET
                        value = EXCLUDED.value
                """),
                {"dt": dt, "name": name, "val": value},
            )

    async def _get_latest_value(self, name: str) -> Optional[float]:
        """DB에서 해당 지표의 최신 값"""
        async with get_session() as session:
            result = session.execute(
                text("""
                    SELECT value FROM global_indicators
                    WHERE indicator_name = :name
                    ORDER BY date DESC LIMIT 1
                """),
                {"name": name},
            )
            row = result.fetchone()
            return float(row[0]) if row else None

    async def get_latest_macro_snapshot(self) -> MacroSnapshot:
        """DB에서 최신 지표를 모아 MacroSnapshot 반환"""
        values: Dict[str, float] = {}
        indicators = [
            "vix", "us10y", "us2y", "yield_spread", "hy_spread",
            "fed_rate", "cpi", "usdkrw", "fsi",
        ]

        async with get_session() as session:
            for name in indicators:
                result = session.execute(
                    text("""
                        SELECT value FROM global_indicators
                        WHERE indicator_name = :name
                        ORDER BY date DESC LIMIT 1
                    """),
                    {"name": name},
                )
                row = result.fetchone()
                values[name] = float(row[0]) if row else 0.0

        # hy_spread 10년 백분위 계산
        hy_percentile = await self._calc_hy_percentile(values.get("hy_spread", 0))

        return MacroSnapshot(
            date=datetime.now(),
            vix=values.get("vix", 0),
            us10y=values.get("us10y", 0),
            us2y=values.get("us2y", 0),
            yield_spread=values.get("yield_spread", 0),
            hy_spread=values.get("hy_spread", 0),
            hy_spread_percentile=hy_percentile,
            fed_rate=values.get("fed_rate", 0),
            cpi_latest=values.get("cpi", 0),
            usdkrw=values.get("usdkrw", 0),
            fsi=values.get("fsi"),
        )

    async def _calc_hy_percentile(self, current_value: float) -> float:
        """하이일드 스프레드의 10년 백분위 계산"""
        if current_value == 0:
            return 50.0

        cutoff = date.today() - timedelta(days=3650)
        async with get_session() as session:
            result = session.execute(
                text("""
                    SELECT COUNT(*) FILTER (WHERE value <= :current),
                           COUNT(*)
                    FROM global_indicators
                    WHERE indicator_name = 'hy_spread'
                      AND date >= :cutoff
                """),
                {"current": current_value, "cutoff": cutoff},
            )
            row = result.fetchone()
            if not row or row[1] == 0:
                return 50.0
            return round(row[0] / row[1] * 100, 1)

    async def close(self) -> None:
        await self._http_client.aclose()
