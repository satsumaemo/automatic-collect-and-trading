"""
이상 상황 주입기.
시나리오의 setup 조건을 시스템 상태에 주입하고, 테스트 후 복원합니다.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from sqlalchemy import text

from app.utils import redis_client
from app.utils.db import get_session

logger = logging.getLogger(__name__)

# 테스트 뉴스 URL 접두사 (복원 시 이것으로 식별)
TEST_URL_PREFIX = "test://fault-injection/"


class FaultInjector:
    """이상 상황 주입기"""

    def __init__(self) -> None:
        # 원래 상태 백업
        self._backup_regime: Optional[str] = None
        self._backup_alert: Optional[str] = None
        self._backup_positions: Optional[dict] = None
        # 주입된 테스트 뉴스 ID
        self._injected_news_ids: List[int] = []
        # 주입된 이벤트
        self._injected_events: List[dict] = []
        # 주입된 시장 데이터 키
        self._injected_price_keys: List[str] = []
        # 오버라이드 상태
        self._morning_analysis: str = ""
        self._portfolio_override: Dict[str, dict] = {}

    async def setup_scenario(self, scenario: dict) -> None:
        """시나리오의 setup dict를 읽어 시스템 상태 조작"""
        setup = scenario.get("setup", {})

        # 원래 상태 백업
        self._backup_regime = await redis_client.get_regime()
        self._backup_alert = await redis_client.get_alert_level()
        self._backup_positions = await redis_client.get_positions()

        # force_regime
        if "force_regime" in setup:
            await redis_client.set_regime(setup["force_regime"])

        # alert_level
        if "alert_level" in setup:
            await redis_client.set_alert_level(setup["alert_level"])

        # morning_analysis
        if "morning_analysis" in setup:
            self._morning_analysis = setup["morning_analysis"]

        # inject_event
        if "inject_event" in setup:
            self._injected_events.append(setup["inject_event"])

        # inject_news
        if "inject_news" in setup:
            await self._inject_news(setup["inject_news"])

        # inject_market_data
        if "inject_market_data" in setup:
            await self._inject_market_data(setup["inject_market_data"])

        # set_portfolio
        if "set_portfolio" in setup:
            self._portfolio_override = setup["set_portfolio"]
            await redis_client.set_positions(
                {k: {"eval_amount": 1_000_000, "pct": v.get("pct", 0)}
                 for k, v in self._portfolio_override.items()}
            )

        # clear_events / clear_negative_news → 이벤트/뉴스 없는 상태 유지
        # (별도 조작 불필요 — 빈 상태가 기본)

    def build_system_state(self) -> dict:
        """현재 주입 상태를 system_state dict로 구성"""
        events_json = json.dumps(self._injected_events, ensure_ascii=False)
        news_summary = ""
        for ev in self._injected_events:
            news_summary += f"이벤트: {ev.get('name', '')} ({ev.get('hours_away', '?')}시간 후)\n"

        return {
            "regime": "expansion",  # 실제로는 Redis에서 읽음
            "alert_level": "normal",
            "portfolio_summary": json.dumps(self._portfolio_override, ensure_ascii=False),
            "daily_trades": "0",
            "morning_analysis": self._morning_analysis or "분석 없음",
            "recent_news": news_summary or "뉴스 없음",
            "upcoming_events": events_json,
        }

    async def restore(self) -> None:
        """원래 상태로 복원"""
        # Redis 원복
        if self._backup_regime is not None:
            await redis_client.set_regime(self._backup_regime)
        if self._backup_alert is not None:
            await redis_client.set_alert_level(self._backup_alert)
        if self._backup_positions is not None:
            await redis_client.set_positions(self._backup_positions)

        # 테스트 뉴스 삭제
        if self._injected_news_ids:
            try:
                async with get_session() as session:
                    await session.execute(
                        text("DELETE FROM news_articles WHERE url LIKE :prefix"),
                        {"prefix": f"{TEST_URL_PREFIX}%"},
                    )
            except Exception as e:
                logger.error("테스트 뉴스 삭제 실패: %s", e)

        # 주입 시장 데이터 삭제
        for key in self._injected_price_keys:
            try:
                r = await redis_client.get_redis()
                await r.delete(key)
            except Exception:
                pass

        # 상태 초기화
        self._injected_news_ids = []
        self._injected_events = []
        self._injected_price_keys = []
        self._morning_analysis = ""
        self._portfolio_override = {}
        self._backup_regime = None
        self._backup_alert = None
        self._backup_positions = None

    async def _inject_news(self, news_cfg: dict) -> None:
        """뉴스 임시 삽입"""
        title = news_cfg.get("title", "테스트 뉴스")
        sentiment = news_cfg.get("sentiment", 0)
        minutes_ago = news_cfg.get("minutes_ago", 30)
        pub_time = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        url = f"{TEST_URL_PREFIX}{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

        try:
            async with get_session() as session:
                result = await session.execute(
                    text("""
                        INSERT INTO news_articles
                            (source, title, url, published_at, language,
                             importance_score, sentiment_score, is_processed)
                        VALUES
                            ('test', :title, :url, :pub, 'ko',
                             0.9, :sent, TRUE)
                        RETURNING article_id
                    """),
                    {"title": title, "url": url, "pub": pub_time, "sent": sentiment},
                )
                row = result.fetchone()
                if row:
                    self._injected_news_ids.append(row[0])
        except Exception as e:
            logger.error("테스트 뉴스 삽입 실패: %s", e)

    async def _inject_market_data(self, data_cfg: dict) -> None:
        """시장 데이터 오버라이드"""
        ticker = data_cfg.get("ticker", "")
        if not ticker:
            return

        key = f"market:price:{ticker}"
        await redis_client.set_price(ticker, 10000, 1_000_000)
        self._injected_price_keys.append(key)
