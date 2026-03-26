"""
시장 데이터 수집기.
KIS 브로커를 사용하여 일봉 OHLCV 수집 + 잔고 동기화.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import text

from app.brokers.kis_broker import KISBroker
from app.utils.db import get_session
from app.utils import redis_client

logger = logging.getLogger(__name__)


def validate_ohlcv(row: dict) -> bool:
    """OHLCV 데이터 유효성 검증"""
    if row["close"] <= 0:
        return False
    if row["volume"] < 0:
        return False
    if row["low"] > row["high"]:
        return False
    if row["open"] < row["low"] or row["open"] > row["high"]:
        # 장 시작 갭 — 경고만, 유효 처리
        logger.warning("시가가 고저 범위 밖: %s", row)
    return True


class MarketCollector:
    """KIS API 기반 시장 데이터 수집"""

    def __init__(self, broker: KISBroker) -> None:
        self.broker = broker
        # ticker → symbol_id 매핑 (sync_symbols에서 로드)
        self._symbol_map: Dict[str, int] = {}
        # symbol_id → ticker 역매핑
        self._id_to_ticker: Dict[int, str] = {}
        # ticker → kis_code 매핑 (DB에서 로드)
        self._kis_code_map: Dict[str, str] = {}
        logger.info("MarketCollector 초기화")

    async def sync_symbols(self) -> None:
        """DB의 활성 종목 목록을 메모리에 캐시 (kis_code 포함)"""
        async with get_session() as session:
            result = session.execute(
                text("SELECT symbol_id, ticker, kis_code FROM symbols WHERE is_active = TRUE")
            )
            rows = result.fetchall()
            self._symbol_map = {row[1]: row[0] for row in rows}
            self._id_to_ticker = {row[0]: row[1] for row in rows}
            self._kis_code_map = {row[1]: row[2] for row in rows if row[2]}

        # KISBroker의 매핑에 DB kis_code 추가
        from app.brokers.kis_broker import ETF_KIS_CODE_MAP
        for ticker, kis_code in self._kis_code_map.items():
            ETF_KIS_CODE_MAP[ticker] = kis_code

        logger.info("종목 목록 동기화: %d개 (kis_code: %d개)", len(self._symbol_map), len(self._kis_code_map))

    def get_symbol_id(self, ticker: str) -> Optional[int]:
        """ticker → symbol_id"""
        return self._symbol_map.get(ticker)

    async def collect_daily_ohlcv(
        self,
        tickers: Optional[List[str]] = None,
        days: int = 5,
    ) -> None:
        """
        일봉 데이터 수집 → DB 저장.
        tickers가 None이면 전체 활성 종목 수집.
        """
        if not self._symbol_map:
            await self.sync_symbols()

        target_tickers = tickers or list(self._symbol_map.keys())
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

        collected = 0
        failed = 0

        for ticker in target_tickers:
            symbol_id = self._symbol_map.get(ticker)
            if symbol_id is None:
                logger.warning("symbol_id 없음: %s — 스킵", ticker)
                continue

            try:
                rows = await self.broker.get_daily_ohlcv(ticker, start_date, end_date)
                if not rows:
                    logger.debug("일봉 데이터 없음: %s", ticker)
                    continue

                await self._save_ohlcv(symbol_id, rows)
                collected += len(rows)

            except ValueError as e:
                # KIS 종목코드 매핑 실패
                logger.warning("종목코드 매핑 실패 [%s]: %s", ticker, e)
                failed += 1
            except Exception as e:
                logger.error("일봉 수집 실패 [%s]: %s", ticker, e)
                failed += 1

        logger.info("일봉 수집 완료: %d건 저장, %d건 실패", collected, failed)

    async def _save_ohlcv(self, symbol_id: int, rows: List[dict]) -> None:
        """일봉 데이터를 DB에 저장 (ON CONFLICT DO UPDATE)"""
        async with get_session() as session:
            for row in rows:
                if not validate_ohlcv(row):
                    logger.warning("유효성 검증 실패 — 스킵: %s", row)
                    continue

                # 날짜 변환: 'YYYYMMDD' → DATE
                date_str = row["date"]
                session.execute(
                    text("""
                        INSERT INTO daily_ohlcv
                            (symbol_id, date, open, high, low, close, volume, turnover)
                        VALUES
                            (:sid, :dt, :o, :h, :l, :c, :v, :t)
                        ON CONFLICT (symbol_id, date) DO UPDATE SET
                            open = EXCLUDED.open,
                            high = EXCLUDED.high,
                            low = EXCLUDED.low,
                            close = EXCLUDED.close,
                            volume = EXCLUDED.volume,
                            turnover = EXCLUDED.turnover
                    """),
                    {
                        "sid": symbol_id,
                        "dt": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}",
                        "o": row["open"],
                        "h": row["high"],
                        "l": row["low"],
                        "c": row["close"],
                        "v": row["volume"],
                        "t": row.get("turnover", 0),
                    },
                )

    async def collect_balance(self) -> None:
        """잔고 조회 → Redis에 포지션/현금 동기화"""
        try:
            balance = await self.broker.get_balance()

            # 포지션 → Redis
            positions = {}
            for pos in balance["positions"]:
                positions[pos["ticker"]] = {
                    "qty": pos["quantity"],
                    "avg_price": pos["avg_price"],
                    "current_price": pos["current_price"],
                    "eval_amount": pos["eval_amount"],
                    "pnl": pos["pnl"],
                    "pnl_pct": pos["pnl_pct"],
                }
            await redis_client.set_positions(positions)

            # 현금 → Redis
            await redis_client.set_cash(krw=float(balance["cash"]))

            logger.info(
                "잔고 동기화: %d종목, 현금 %s원",
                len(positions),
                f"{balance['cash']:,}",
            )
        except Exception as e:
            logger.error("잔고 동기화 실패: %s", e)
