"""
모의투자 체결 결과를 현실적으로 보정하는 슬리피지 시뮬레이터.

모의투자(paper) 모드에서만 작동합니다.
실전에서는 결과를 그대로 반환합니다.

보정 항목:
  1. 스프레드 기반 슬리피지 (ETF별 차등)
  2. 랜덤 시장충격 (소규모)
  3. 부분 체결 시뮬레이션 (8% 확률)
  4. 수수료/세금 보정 (모의투자는 비용 미반영)
"""

import logging
import random
from typing import Any

from app.config import settings
from app.models.contracts import ExecutionResult, OrderSide

logger = logging.getLogger(__name__)

# ── ETF별 예상 스프레드 (유동성 기반) ──
SPREAD_TABLE = {
    # 대형 (낮은 스프레드)
    "KODEX 200": 0.0003,
    "TIGER 200": 0.0003,
    "TIGER 미국S&P500": 0.0005,
    "TIGER 미국나스닥100": 0.0005,
    # 중형
    "KODEX 반도체": 0.0008,
    "TIGER AI반도체핵심공정": 0.0010,
    "TIGER 미국필라델피아반도체나스닥": 0.0008,
    "KODEX 국고채10년": 0.0008,
    "TIGER 단기채권": 0.0005,
    "KODEX 종합채권": 0.0008,
    "TIGER 미국채10년선물": 0.0010,
    "ACE 미국30년국채": 0.0012,
    "KODEX 은행": 0.0008,
    "TIGER 200금융": 0.0008,
    # 소형/테마 (높은 스프레드)
    "KODEX 골드선물(H)": 0.0015,
    "ACE KRX금현물": 0.0015,
    "TIGER 2차전지테마": 0.0010,
    "KODEX 2차전지산업": 0.0010,
    "KODEX 바이오": 0.0012,
    "TIGER 헬스케어": 0.0012,
    "TIGER 우주방산": 0.0012,
    "KODEX 코스닥150": 0.0005,
}
DEFAULT_SPREAD = 0.0010

# ETF 키워드
_ETF_KW = ("KODEX", "TIGER", "ACE")


class SlippageSimulator:
    """모의투자 체결 현실화 시뮬레이터"""

    def apply(self, result: ExecutionResult, order: Any) -> ExecutionResult:
        """
        체결 결과에 현실적 슬리피지를 적용.
        paper 모드에서만 작동, 실전에서는 그대로 반환.
        """
        if not settings.is_paper:
            return result

        if result.filled_quantity <= 0 or result.filled_price <= 0:
            return result

        original_price = result.filled_price

        # 1. 스프레드 기반 슬리피지
        spread_pct = SPREAD_TABLE.get(result.ticker, DEFAULT_SPREAD)
        half_spread = original_price * spread_pct * 0.5
        if result.side == OrderSide.BUY:
            result.filled_price += half_spread
        else:
            result.filled_price -= half_spread

        # 2. 랜덤 시장충격 (소규모: 0.01%~0.05%)
        impact = original_price * random.uniform(0.0001, 0.0005)
        if result.side == OrderSide.BUY:
            result.filled_price += impact
        else:
            result.filled_price -= impact

        # 가격은 음수가 되면 안 됨
        result.filled_price = max(result.filled_price, 1.0)

        # 3. 부분 체결 시뮬레이션 (8% 확률)
        if random.random() < 0.08:
            fill_ratio = random.uniform(0.50, 0.95)
            result.filled_quantity = max(1, int(result.quantity * fill_ratio))
            result.status = "partial"
            logger.info(
                "[슬리피지 시뮬] 부분 체결: %s %.0f%%",
                result.ticker, fill_ratio * 100,
            )

        # 4. 슬리피지 비율 기록
        if original_price > 0:
            result.slippage_pct = round(
                abs(result.filled_price - original_price) / original_price, 6
            )

        # 5. 비용 보정 (모의투자는 수수료/세금이 빠지지 않으므로 강제 적용)
        filled_amount = result.filled_quantity * result.filled_price

        if result.commission == 0:
            # 국내 ETF 기본 수수료 0.015%
            result.commission = round(filled_amount * 0.00015, 2)

        if result.tax == 0 and result.side == OrderSide.SELL:
            is_etf = any(kw in result.ticker for kw in _ETF_KW)
            if not is_etf:
                result.tax = round(filled_amount * 0.0018, 2)

        return result
