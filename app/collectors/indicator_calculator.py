"""
기술적 지표 계산기.
일봉 데이터를 기반으로 기술적 지표를 계산하여 daily_indicators 테이블에 저장합니다.

계산 지표:
  이동평균: MA5, MA20, MA60, MA120
  모멘텀:   RSI(14), MACD(12,26,9)
  변동성:   볼린저밴드(20,2), ATR(14)
  추세:     ADX(14), OBV
"""

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import ta

from sqlalchemy import text

from app.utils.db import get_session

logger = logging.getLogger(__name__)

# 지표 계산에 필요한 최소 일봉 수 (MA120 기준)
MIN_BARS = 130


class IndicatorCalculator:
    """기술적 지표 계산 및 DB 저장"""

    def __init__(self) -> None:
        # ticker → symbol_id 매핑 (외부에서 주입)
        self._symbol_map: Dict[str, int] = {}
        logger.info("IndicatorCalculator 초기화")

    def set_symbol_map(self, symbol_map: Dict[str, int]) -> None:
        self._symbol_map = symbol_map

    async def calculate_all_indicators(self) -> None:
        """전체 활성 종목의 기술적 지표 계산"""
        if not self._symbol_map:
            await self._load_symbol_map()

        calculated = 0
        failed = 0

        for ticker, symbol_id in self._symbol_map.items():
            try:
                await self.calculate_indicators(symbol_id)
                calculated += 1
            except Exception as e:
                logger.error("지표 계산 실패 [%s]: %s", ticker, e)
                failed += 1

        logger.info("기술적 지표 계산 완료: %d건 성공, %d건 실패", calculated, failed)

    async def calculate_indicators(self, symbol_id: int) -> None:
        """단일 종목의 기술적 지표 계산 → DB 저장"""
        # DB에서 최근 200일 일봉 조회
        df = await self._load_ohlcv(symbol_id, limit=200)
        if df is None or len(df) < MIN_BARS:
            return

        # 지표 계산
        df = self._compute_indicators(df)

        # 최근 5일치만 DB에 저장 (이전 데이터는 이미 저장되어 있음)
        recent = df.tail(5)
        await self._save_indicators(symbol_id, recent)

    async def _load_ohlcv(self, symbol_id: int, limit: int = 200) -> Optional[pd.DataFrame]:
        """DB에서 일봉 데이터를 DataFrame으로 로드"""
        async with get_session() as session:
            result = session.execute(
                text("""
                    SELECT date, open, high, low, close, volume
                    FROM daily_ohlcv
                    WHERE symbol_id = :sid
                    ORDER BY date DESC
                    LIMIT :lim
                """),
                {"sid": symbol_id, "lim": limit},
            )
            rows = result.fetchall()

        if not rows:
            return None

        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
        # float 변환
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        df["volume"] = df["volume"].astype(int)
        # 날짜 오름차순 정렬
        df = df.sort_values("date").reset_index(drop=True)
        return df

    @staticmethod
    def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """ta 라이브러리로 기술적 지표 계산"""
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"].astype(float)

        # 이동평균
        df["ma5"] = ta.trend.SMAIndicator(close, window=5).sma_indicator()
        df["ma20"] = ta.trend.SMAIndicator(close, window=20).sma_indicator()
        df["ma60"] = ta.trend.SMAIndicator(close, window=60).sma_indicator()
        df["ma120"] = ta.trend.SMAIndicator(close, window=120).sma_indicator()

        # RSI
        df["rsi14"] = ta.momentum.RSIIndicator(close, window=14).rsi()

        # MACD
        macd_ind = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
        df["macd"] = macd_ind.macd()
        df["macd_signal"] = macd_ind.macd_signal()

        # 볼린저밴드
        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_lower"] = bb.bollinger_lband()

        # ATR
        df["atr14"] = ta.volatility.AverageTrueRange(
            high, low, close, window=14
        ).average_true_range()

        # ADX
        df["adx14"] = ta.trend.ADXIndicator(high, low, close, window=14).adx()

        # OBV
        df["obv"] = ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()

        return df

    async def _save_indicators(self, symbol_id: int, df: pd.DataFrame) -> None:
        """계산된 지표를 daily_indicators 테이블에 저장"""
        async with get_session() as session:
            for _, row in df.iterrows():
                # NaN은 None으로 변환
                def val(v: float) -> Optional[float]:
                    return None if pd.isna(v) else float(v)

                def val_int(v: float) -> Optional[int]:
                    return None if pd.isna(v) else int(v)

                session.execute(
                    text("""
                        INSERT INTO daily_indicators
                            (symbol_id, date, ma5, ma20, ma60, ma120,
                             rsi14, macd, macd_signal, bb_upper, bb_lower,
                             atr14, adx14, obv)
                        VALUES
                            (:sid, :dt, :ma5, :ma20, :ma60, :ma120,
                             :rsi14, :macd, :macd_signal, :bb_upper, :bb_lower,
                             :atr14, :adx14, :obv)
                        ON CONFLICT (symbol_id, date) DO UPDATE SET
                            ma5 = EXCLUDED.ma5,
                            ma20 = EXCLUDED.ma20,
                            ma60 = EXCLUDED.ma60,
                            ma120 = EXCLUDED.ma120,
                            rsi14 = EXCLUDED.rsi14,
                            macd = EXCLUDED.macd,
                            macd_signal = EXCLUDED.macd_signal,
                            bb_upper = EXCLUDED.bb_upper,
                            bb_lower = EXCLUDED.bb_lower,
                            atr14 = EXCLUDED.atr14,
                            adx14 = EXCLUDED.adx14,
                            obv = EXCLUDED.obv
                    """),
                    {
                        "sid": symbol_id,
                        "dt": row["date"],
                        "ma5": val(row.get("ma5")),
                        "ma20": val(row.get("ma20")),
                        "ma60": val(row.get("ma60")),
                        "ma120": val(row.get("ma120")),
                        "rsi14": val(row.get("rsi14")),
                        "macd": val(row.get("macd")),
                        "macd_signal": val(row.get("macd_signal")),
                        "bb_upper": val(row.get("bb_upper")),
                        "bb_lower": val(row.get("bb_lower")),
                        "atr14": val(row.get("atr14")),
                        "adx14": val(row.get("adx14")),
                        "obv": val_int(row.get("obv")),
                    },
                )

    async def _load_symbol_map(self) -> None:
        """DB에서 활성 종목 매핑 로드"""
        async with get_session() as session:
            result = session.execute(
                text("SELECT ticker, symbol_id FROM symbols WHERE is_active = TRUE")
            )
            self._symbol_map = {row[0]: row[1] for row in result.fetchall()}
