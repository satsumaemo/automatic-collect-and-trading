"""
DataService — 데이터 수집 및 제공 서비스.

역할: 모든 데이터의 수집·저장·조회를 담당하는 단일 창구.
      4개 collector를 통합하여 일배치/시간별 수집을 오케스트레이션합니다.
의존성: KISBroker, MarketCollector, MacroCollector, NewsCollector, IndicatorCalculator
"""

import logging
from datetime import datetime, timezone, timedelta, date
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy import text

from app.brokers.kis_broker import KISBroker
from app.collectors.market_collector import MarketCollector
from app.collectors.macro_collector import MacroCollector
from app.collectors.news_collector import NewsCollector
from app.collectors.indicator_calculator import IndicatorCalculator
from app.models.contracts import (
    BuzzData,
    EconomicEvent,
    ETFFlowData,
    FinancialData,
    IndicatorData,
    InsiderTrade,
    MacroSnapshot,
    NewsSummary,
    PolymarketSignal,
    PriceData,
    SupplyDemandData,
)
from app.utils.db import get_session
from app.utils import redis_client

logger = logging.getLogger(__name__)


class DataService:
    """시장 데이터 수집 및 제공"""

    def __init__(self) -> None:
        # 브로커
        self.broker = KISBroker()

        # 수집기
        self.market_collector = MarketCollector(self.broker)
        self.macro_collector = MacroCollector()
        self.news_collector = NewsCollector()
        self.indicator_calculator = IndicatorCalculator()

        # ticker → symbol_id 매핑
        self._symbol_map: Dict[str, int] = {}
        self._id_to_ticker: Dict[int, str] = {}

        logger.info("DataService 초기화")

    async def _ensure_symbol_map(self) -> None:
        """symbol_map이 비어있으면 DB에서 로드"""
        if not self._symbol_map:
            await self.market_collector.sync_symbols()
            self._symbol_map = self.market_collector._symbol_map
            self._id_to_ticker = self.market_collector._id_to_ticker
            # indicator_calculator에도 주입
            self.indicator_calculator.set_symbol_map(self._symbol_map)

    # ═══════════════════════════════════════
    # 조회 메서드
    # ═══════════════════════════════════════

    async def get_daily_ohlcv(self, ticker: str, days: int = 120) -> pd.DataFrame:
        """일봉 데이터 조회 → DataFrame"""
        await self._ensure_symbol_map()
        symbol_id = self._symbol_map.get(ticker)
        if symbol_id is None:
            logger.warning("symbol_id 없음: %s", ticker)
            return pd.DataFrame()

        cutoff = date.today() - timedelta(days=days)

        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT date, open, high, low, close, adj_close, volume
                    FROM daily_ohlcv
                    WHERE symbol_id = :sid AND date >= :cutoff
                    ORDER BY date ASC
                """),
                {"sid": symbol_id, "cutoff": cutoff},
            )
            rows = result.fetchall()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(
            rows, columns=["date", "open", "high", "low", "close", "adj_close", "volume"]
        )
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        df["adj_close"] = df["adj_close"].astype(float) if df["adj_close"].notna().any() else df["close"]
        df["volume"] = df["volume"].astype(int)
        return df

    async def get_realtime_price(self, ticker: str) -> PriceData:
        """실시간 시세 조회 (Redis 캐시 우선 → KIS API 폴백)"""
        # Redis 캐시 확인
        cached = await redis_client.get_price(ticker)
        if cached:
            return PriceData(
                ticker=ticker,
                price=float(cached["price"]),
                volume=int(cached["volume"]),
                timestamp=datetime.fromisoformat(cached["ts"]),
            )

        # 캐시 미스 → API 호출
        try:
            detail = await self.broker.get_market_price_detail(ticker)
            price = detail["price"]
            volume = detail["volume"]

            # Redis에 캐시
            await redis_client.set_price(ticker, price, volume)

            return PriceData(
                ticker=ticker,
                price=price,
                volume=volume,
                timestamp=datetime.now(),
            )
        except Exception as e:
            logger.error("실시간 시세 조회 실패 [%s]: %s", ticker, e)
            raise

    async def get_indicators(self, ticker: str) -> IndicatorData:
        """최신 기술적 지표 조회"""
        await self._ensure_symbol_map()
        symbol_id = self._symbol_map.get(ticker)
        if symbol_id is None:
            raise ValueError(f"symbol_id 없음: {ticker}")

        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT date, ma5, ma20, ma60, ma120,
                           rsi14, macd, macd_signal, bb_upper, bb_lower,
                           atr14, adx14, obv
                    FROM daily_indicators
                    WHERE symbol_id = :sid
                    ORDER BY date DESC LIMIT 1
                """),
                {"sid": symbol_id},
            )
            row = result.fetchone()

        if not row:
            raise ValueError(f"지표 데이터 없음: {ticker}")

        def fval(v) -> Optional[float]:
            return float(v) if v is not None else None

        def ival(v) -> Optional[int]:
            return int(v) if v is not None else None

        return IndicatorData(
            ticker=ticker,
            date=row[0],
            ma5=fval(row[1]),
            ma20=fval(row[2]),
            ma60=fval(row[3]),
            ma120=fval(row[4]),
            rsi14=fval(row[5]),
            macd=fval(row[6]),
            macd_signal=fval(row[7]),
            bb_upper=fval(row[8]),
            bb_lower=fval(row[9]),
            atr14=fval(row[10]),
            adx14=fval(row[11]),
            obv=ival(row[12]),
        )

    async def get_macro_snapshot(self) -> MacroSnapshot:
        """거시경제 지표 스냅샷"""
        return await self.macro_collector.get_latest_macro_snapshot()

    async def get_news_summary(self, hours: int = 24) -> NewsSummary:
        """최근 N시간 뉴스 요약"""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        async with get_session() as session:
            # 총 기사 수
            count_result = await session.execute(
                text("""
                    SELECT COUNT(*) FROM news_articles
                    WHERE published_at >= :cutoff
                """),
                {"cutoff": cutoff},
            )
            total = count_result.scalar() or 0

            # 중요도 상위 20건
            top_result = await session.execute(
                text("""
                    SELECT title, summary, source, categories,
                           importance_score, published_at
                    FROM news_articles
                    WHERE published_at >= :cutoff
                    ORDER BY importance_score DESC NULLS LAST
                    LIMIT 20
                """),
                {"cutoff": cutoff},
            )
            top_rows = top_result.fetchall()

            key_articles = []
            for r in top_rows:
                key_articles.append({
                    "title": r[0],
                    "summary": (r[1] or "")[:200],
                    "source": r[2],
                    "categories": r[3] or [],
                    "importance": float(r[4]) if r[4] else 0,
                    "published_at": str(r[5]),
                })

            # 카테고리별 집계
            cat_result = await session.execute(
                text("""
                    SELECT unnest(categories) AS cat, COUNT(*) AS cnt
                    FROM news_articles
                    WHERE published_at >= :cutoff
                    GROUP BY cat
                    ORDER BY cnt DESC
                """),
                {"cutoff": cutoff},
            )
            sector_summary = {row[0]: row[1] for row in cat_result.fetchall()}

            # 버즈 상위 토픽
            buzz_result = await session.execute(
                text("""
                    SELECT category, buzz_score, tone_shift
                    FROM news_frequency_daily
                    WHERE date = :today
                    ORDER BY buzz_score DESC
                    LIMIT 5
                """),
                {"today": date.today()},
            )
            buzzing = [
                {"category": r[0], "buzz_score": float(r[1] or 0), "tone_shift": float(r[2] or 0)}
                for r in buzz_result.fetchall()
            ]

        return NewsSummary(
            date=datetime.now(),
            total_articles_24h=total,
            top_buzzing_topics=buzzing,
            key_articles=key_articles,
            sector_summary=sector_summary,
        )

    async def get_news_buzz(self, category: str) -> BuzzData:
        """뉴스 빈도 변화 조회"""
        today = date.today()

        async with get_session() as session:
            # 최근 7일 평균 buzz
            result_7d = await session.execute(
                text("""
                    SELECT AVG(buzz_score), AVG(tone_shift), AVG(avg_sentiment)
                    FROM news_frequency_daily
                    WHERE category = :cat AND date >= :cutoff
                """),
                {"cat": category, "cutoff": today - timedelta(days=7)},
            )
            row_7d = result_7d.fetchone()

            # 최근 30일 평균 buzz
            result_30d = await session.execute(
                text("""
                    SELECT AVG(buzz_score)
                    FROM news_frequency_daily
                    WHERE category = :cat AND date >= :cutoff
                """),
                {"cat": category, "cutoff": today - timedelta(days=30)},
            )
            row_30d = result_30d.fetchone()

        avg_7d = float(row_7d[0]) if row_7d and row_7d[0] else 1.0
        avg_30d = float(row_30d[0]) if row_30d and row_30d[0] else 1.0
        tone_shift = float(row_7d[1]) if row_7d and row_7d[1] else 0.0
        current_tone = float(row_7d[2]) if row_7d and row_7d[2] else 0.0

        return BuzzData(
            category=category,
            buzz_score=avg_7d / avg_30d if avg_30d > 0 else 1.0,
            tone_shift=tone_shift,
            current_tone=current_tone,
        )

    async def get_financial_data(self, company_id: int) -> FinancialData:
        """재무제표 요약 조회"""
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT fs.company_id, s.ticker,
                           fs.fiscal_year, fs.fiscal_quarter,
                           fs.revenue, fs.operating_income, fs.net_income,
                           fs.roe, fs.debt_ratio,
                           CASE WHEN fs.revenue > 0
                                THEN fs.operating_income::float / fs.revenue
                                ELSE 0 END AS operating_margin,
                           fs.fcf, fs.quality_score
                    FROM financial_statements fs
                    LEFT JOIN symbols s ON s.symbol_id = fs.company_id
                    WHERE fs.company_id = :cid
                    ORDER BY fs.fiscal_year DESC, fs.fiscal_quarter DESC
                    LIMIT 1
                """),
                {"cid": company_id},
            )
            row = result.fetchone()

        if not row:
            raise ValueError(f"재무 데이터 없음: company_id={company_id}")

        return FinancialData(
            company_id=row[0],
            ticker=row[1] or "",
            fiscal_year=row[2],
            fiscal_quarter=row[3],
            revenue=int(row[4] or 0),
            operating_income=int(row[5] or 0),
            net_income=int(row[6] or 0),
            roe=float(row[7] or 0),
            debt_ratio=float(row[8] or 0),
            operating_margin=float(row[9] or 0),
            fcf=int(row[10] or 0),
            quality_score=int(row[11] or 0),
        )

    async def get_economic_events(self, days: int = 3) -> List[EconomicEvent]:
        """향후 N일 경제 이벤트 목록"""
        today = date.today()
        end = today + timedelta(days=days)

        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT event_name, event_date, event_time, importance
                    FROM economic_events
                    WHERE event_date BETWEEN :start AND :end
                    ORDER BY event_date, event_time
                """),
                {"start": today, "end": end},
            )
            rows = result.fetchall()

        events = []
        now = datetime.now()
        for row in rows:
            event_dt = datetime.combine(row[1], row[2]) if row[2] else datetime.combine(row[1], datetime.min.time())
            hours_away = max((event_dt - now).total_seconds() / 3600, 0)
            events.append(EconomicEvent(
                name=row[0],
                date=event_dt,
                importance=row[3],
                hours_away=round(hours_away, 1),
            ))
        return events

    async def get_polymarket_alerts(self) -> List[PolymarketSignal]:
        """폴리마켓 이상 신호 조회 (미구현 — 빈 리스트 반환)"""
        # TODO: Polymarket API 연동
        return []

    async def get_etf_flows(self, sector: str, days: int = 30) -> ETFFlowData:
        """ETF 자금흐름 조회"""
        await self._ensure_symbol_map()

        # 해당 섹터의 ETF symbol_id 목록
        sector_ids = []
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT symbol_id FROM symbols
                    WHERE sector = :sector AND is_active = TRUE
                """),
                {"sector": sector},
            )
            sector_ids = [row[0] for row in result.fetchall()]

        if not sector_ids:
            return ETFFlowData(sector=sector, net_flow_7d=0, net_flow_30d_avg=0, flow_ratio=1.0)

        today = date.today()
        async with get_session() as session:
            # 최근 7일 순유입
            result_7d = await session.execute(
                text("""
                    SELECT COALESCE(SUM(net_flow), 0)
                    FROM etf_flows
                    WHERE symbol_id = ANY(:ids) AND date >= :cutoff
                """),
                {"ids": sector_ids, "cutoff": today - timedelta(days=7)},
            )
            flow_7d = float(result_7d.scalar() or 0)

            # 최근 30일 일평균
            result_30d = await session.execute(
                text("""
                    SELECT COALESCE(AVG(daily_sum), 0) FROM (
                        SELECT date, SUM(net_flow) AS daily_sum
                        FROM etf_flows
                        WHERE symbol_id = ANY(:ids) AND date >= :cutoff
                        GROUP BY date
                    ) sub
                """),
                {"ids": sector_ids, "cutoff": today - timedelta(days=days)},
            )
            avg_30d = float(result_30d.scalar() or 0)

        flow_ratio = flow_7d / (avg_30d * 7) if avg_30d != 0 else 1.0

        return ETFFlowData(
            sector=sector,
            net_flow_7d=flow_7d,
            net_flow_30d_avg=avg_30d,
            flow_ratio=round(flow_ratio, 4),
        )

    async def get_supply_demand(self, ticker: str, days: int = 20) -> SupplyDemandData:
        """수급 데이터 조회"""
        await self._ensure_symbol_map()
        symbol_id = self._symbol_map.get(ticker)
        if symbol_id is None:
            raise ValueError(f"symbol_id 없음: {ticker}")

        cutoff = date.today() - timedelta(days=days)
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT date, foreign_net, institution_net, individual_net
                    FROM supply_demand
                    WHERE symbol_id = :sid AND date >= :cutoff
                    ORDER BY date DESC LIMIT 1
                """),
                {"sid": symbol_id, "cutoff": cutoff},
            )
            row = result.fetchone()

        if not row:
            return SupplyDemandData(
                ticker=ticker, date=datetime.now(),
                foreign_net=0, institution_net=0, individual_net=0,
            )

        return SupplyDemandData(
            ticker=ticker,
            date=datetime.combine(row[0], datetime.min.time()),
            foreign_net=int(row[1] or 0),
            institution_net=int(row[2] or 0),
            individual_net=int(row[3] or 0),
        )

    async def get_insider_trades(self, company_id: int, days: int = 90) -> List[InsiderTrade]:
        """내부자 거래 조회"""
        cutoff = date.today() - timedelta(days=days)
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT company_id, insider_name, position, trade_type,
                           shares, price, total_value, trade_date, report_date
                    FROM insider_trades
                    WHERE company_id = :cid AND trade_date >= :cutoff
                    ORDER BY trade_date DESC
                """),
                {"cid": company_id, "cutoff": cutoff},
            )
            rows = result.fetchall()

        return [
            InsiderTrade(
                company_id=r[0],
                insider_name=r[1] or "",
                position=r[2] or "",
                trade_type=r[3],
                shares=int(r[4]),
                price=float(r[5] or 0),
                total_value=int(r[6] or 0),
                trade_date=datetime.combine(r[7], datetime.min.time()),
                report_date=datetime.combine(r[8], datetime.min.time()) if r[8] else datetime.now(),
            )
            for r in rows
        ]

    # ═══════════════════════════════════════
    # 수집 메서드
    # ═══════════════════════════════════════

    async def collect_daily_batch(self) -> None:
        """일배치 수집 — 매일 07:00 실행"""
        logger.info("=== 일배치 수집 시작 ===")

        # 1. KIS 토큰 갱신 확인
        try:
            await self.broker.refresh_token()
            logger.info("[1/7] KIS 토큰 갱신 완료")
        except Exception as e:
            logger.error("[1/7] KIS 토큰 갱신 실패: %s", e)

        # 2. 종목 목록 동기화
        try:
            await self._ensure_symbol_map()
            logger.info("[2/7] 종목 목록 동기화 완료 (%d개)", len(self._symbol_map))
        except Exception as e:
            logger.error("[2/7] 종목 목록 동기화 실패: %s", e)

        # 3. 일봉 OHLCV 수집 (전 종목)
        try:
            await self.market_collector.collect_daily_ohlcv()
            logger.info("[3/7] 일봉 수집 완료")
        except Exception as e:
            logger.error("[3/7] 일봉 수집 실패: %s", e)

        # 4. 잔고 동기화
        try:
            await self.market_collector.collect_balance()
            logger.info("[4/7] 잔고 동기화 완료")
        except Exception as e:
            logger.error("[4/7] 잔고 동기화 실패: %s", e)

        # 5. 글로벌 매크로 지표 수집
        try:
            await self.macro_collector.collect_all()
            logger.info("[5/7] 매크로 지표 수집 완료")
        except Exception as e:
            logger.error("[5/7] 매크로 지표 수집 실패: %s", e)

        # 6. 뉴스 수집
        try:
            await self.news_collector.collect_all_feeds()
            await self.news_collector.update_daily_frequency()
            logger.info("[6/7] 뉴스 수집 완료")
        except Exception as e:
            logger.error("[6/7] 뉴스 수집 실패: %s", e)

        # 7. 기술적 지표 계산
        try:
            await self.indicator_calculator.calculate_all_indicators()
            logger.info("[7/7] 기술적 지표 계산 완료")
        except Exception as e:
            logger.error("[7/7] 기술적 지표 계산 실패: %s", e)

        # 하트비트 갱신
        await redis_client.set_heartbeat()
        logger.info("=== 일배치 수집 완료 ===")

    async def collect_hourly(self) -> None:
        """매시간 경량 수집 — 매크로 + 뉴스"""
        logger.info("시간별 수집 시작")

        try:
            await self.macro_collector.collect_all()
        except Exception as e:
            logger.error("매크로 지표 수집 실패: %s", e)

        try:
            await self.news_collector.collect_all_feeds()
        except Exception as e:
            logger.error("뉴스 수집 실패: %s", e)

        await redis_client.set_heartbeat()
        logger.info("시간별 수집 완료")
