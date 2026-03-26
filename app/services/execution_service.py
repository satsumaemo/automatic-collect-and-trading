"""
ExecutionService — Layer 6: 주문 실행.

ApprovedOrder를 받아 KIS API로 실제 주문을 실행합니다.
호가단위 보정, 부분 체결 처리, 비용 계산, 슬리피지 시뮬레이션을 수행합니다.

의존성: BaseBroker
"""

import asyncio
import logging
import math
import uuid
from datetime import datetime
from typing import List, Optional

from app.brokers.base_broker import BaseBroker
from app.config import settings
from app.models.contracts import ApprovedOrder, ExecutionResult, OrderSide, OrderTrigger

logger = logging.getLogger(__name__)

# ── 호가단위 테이블 (국내 주식) ──
TICK_TABLE_KR = [
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
    (float("inf"), 1_000),
]
TICK_ETF = 5  # ETF는 가격 무관 5원

# ETF 키워드
ETF_KEYWORDS = ("KODEX", "TIGER", "ACE", "ARIRANG", "HANARO", "KBSTAR", "SOL")

# 해외 ETF 키워드
OVERSEAS_KEYWORDS = ("미국", "S&P", "나스닥", "미국채", "미국달러")


class ExecutionService:
    """주문 실행 및 체결 관리"""

    def __init__(self, broker: Optional[BaseBroker] = None) -> None:
        self.broker = broker
        self._is_paper = settings.is_paper

        # 슬리피지 시뮬레이터 (모의투자 전용)
        self._slippage_sim = None
        if self._is_paper:
            try:
                from tests.slippage_simulator.simulator import SlippageSimulator
                self._slippage_sim = SlippageSimulator()
            except ImportError:
                logger.debug("SlippageSimulator 미사용")

        logger.info("ExecutionService 초기화 (모드: %s)", settings.mode)

    # ═══════════════════════════════════════
    # 주문 실행 메인
    # ═══════════════════════════════════════

    async def execute(self, order: ApprovedOrder) -> ExecutionResult:
        """주문 실행 → 체결 결과 반환"""
        order_id = (
            f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        )

        # 브로커 미설정 → 시뮬레이션 체결
        if self.broker is None:
            logger.info("브로커 미설정 — 시뮬레이션 체결: %s %s x%d",
                        order.side.value, order.ticker, order.quantity)
            return self._simulate_execution(order, order_id)

        try:
            # 1. 호가단위 보정
            price = order.price
            if price is not None:
                price = self.align_tick_size(price, order.ticker, order.side)

            # 2. API 주문 제출
            response = await self.broker.submit_order({
                "ticker": order.ticker,
                "side": order.side.value,
                "quantity": order.quantity,
                "price": price,
            })
            api_order_id = response.get("order_id", order_id)

            # 3. 체결 확인
            fill = await self._wait_for_fill(api_order_id, timeout=60)

            filled_price = float(fill.get("filled_price", price or 0))
            filled_qty = int(fill.get("filled_quantity", order.quantity))
            status = fill.get("status", "filled")

            # 3-1. 잔고 폴백으로 체결 복구 시도
            if status == "timeout" and fill.get("balance_positions"):
                kis_code = self.broker.resolve_kis_code(order.ticker)
                for pos in fill["balance_positions"]:
                    if pos.get("ticker") == kis_code:
                        filled_qty = pos["quantity"]
                        filled_price = pos["current_price"]
                        status = "filled"
                        logger.info(
                            "잔고 폴백으로 체결 복구: %s x%d @%.0f",
                            order.ticker, filled_qty, filled_price,
                        )
                        break

            # 4. 비용 계산
            amount = filled_qty * filled_price
            commission = self._calc_commission(amount, order.ticker)
            tax = self._calc_tax(amount, order.ticker, order.side)
            slippage = (
                abs(filled_price - (price or filled_price)) / max(filled_price, 1)
                if filled_price > 0
                else 0
            )

            result = ExecutionResult(
                order_id=api_order_id,
                ticker=order.ticker,
                side=order.side,
                quantity=order.quantity,
                filled_quantity=filled_qty,
                filled_price=filled_price,
                status=status,
                commission=commission,
                tax=tax,
                slippage_pct=round(slippage, 6),
                trigger=order.trigger,
            )

            # 5. 모의투자 슬리피지 보정
            if self._slippage_sim and self._is_paper:
                result = self._slippage_sim.apply(result, order)

            # 6. 부분 체결 처리
            if status == "partial":
                result = await self.handle_partial_fill(result)

            logger.info(
                "체결: %s %s x%d @%s [%s] 수수료=%s 세금=%s 슬리피지=%.4f%%",
                order.side.value, order.ticker, filled_qty,
                f"{filled_price:,.0f}", status,
                f"{commission:,.0f}", f"{tax:,.0f}", slippage * 100,
            )
            return result

        except Exception as e:
            logger.error("주문 실행 실패 [%s]: %s", order.ticker, e)
            return ExecutionResult(
                order_id=order_id,
                ticker=order.ticker,
                side=order.side,
                quantity=order.quantity,
                filled_quantity=0,
                filled_price=0,
                status="error",
                commission=0,
                tax=0,
                slippage_pct=0,
                trigger=order.trigger,
            )

    # ═══════════════════════════════════════
    # 호가단위 보정
    # ═══════════════════════════════════════

    def align_tick_size(
        self, price: float, ticker: str, side: OrderSide = OrderSide.BUY
    ) -> float:
        """호가단위에 맞게 가격 보정. 매수=올림, 매도=내림."""
        is_etf = any(kw in ticker for kw in ETF_KEYWORDS)

        if is_etf:
            tick = TICK_ETF
        else:
            tick = 1
            for threshold, t in TICK_TABLE_KR:
                if price < threshold:
                    tick = t
                    break

        if side == OrderSide.BUY:
            return math.ceil(price / tick) * tick
        else:
            return math.floor(price / tick) * tick

    # ═══════════════════════════════════════
    # 체결 확인 폴링
    # ═══════════════════════════════════════

    async def _wait_for_fill(self, order_id: str, timeout: int = 60) -> dict:
        """
        체결 확인.
        - 모의투자: 2초 초기 대기 후 2초 간격, 최대 3회 폴링 (총 ~8초)
        - 실전: 1초 간격, 최대 timeout초 폴링
        타임아웃 시 취소 시도 → 이미 체결이면 체결 조회 재시도 → 잔고 폴백
        """
        if self._is_paper:
            initial_delay = 2.0
            poll_interval = 2.0
            max_polls = 3
        else:
            initial_delay = 1.0
            poll_interval = 1.0
            max_polls = timeout

        # 초기 대기 (KIS 서버 체결 처리 시간)
        await asyncio.sleep(initial_delay)

        for i in range(max_polls):
            try:
                status = await self.broker.get_order_status(order_id)
                st = status.get("status")
                if st in ("filled", "partial", "rejected", "cancelled"):
                    return status
                if st == "pending":
                    logger.debug("체결 대기 중 (%d/%d): %s", i + 1, max_polls, order_id)
            except Exception as e:
                logger.debug("체결 확인 에러 (%d/%d): %s", i + 1, max_polls, e)

            if i < max_polls - 1:
                await asyncio.sleep(poll_interval)

        # 폴링 타임아웃 → 취소 시도
        logger.warning("체결 확인 타임아웃: %s", order_id)
        try:
            await self.broker.cancel_order(order_id)
        except RuntimeError as e:
            if "already_filled" in str(e):
                # 이미 체결됨 → 체결 조회 재시도
                logger.info("취소 불가 (이미 체결) — 체결 정보 재조회: %s", order_id)
                return await self._retry_fill_query(order_id)
        except Exception:
            pass

        # 최종 폴백: 잔고 조회로 보유 확인
        return await self._fallback_balance_check(order_id)

    async def _retry_fill_query(self, order_id: str) -> dict:
        """이미 체결된 주문의 체결 정보를 재조회 (최대 2회)"""
        for attempt in range(2):
            await asyncio.sleep(1.0)
            try:
                status = await self.broker.get_order_status(order_id)
                if status.get("status") in ("filled", "partial"):
                    return status
            except Exception as e:
                logger.debug("체결 재조회 에러 (%d/2): %s", attempt + 1, e)

        logger.warning("체결 재조회 실패 — 잔고 폴백: %s", order_id)
        return await self._fallback_balance_check(order_id)

    async def _fallback_balance_check(self, order_id: str) -> dict:
        """
        잔고 조회(inquire-balance)로 실제 보유 여부 확인.
        체결 조회 실패 시 마지막 폴백.
        """
        try:
            balance = await self.broker.get_balance()
            positions = balance.get("positions", [])
            if positions:
                logger.info(
                    "잔고 폴백: %d개 종목 보유 확인 (주문 %s 체결 추정)",
                    len(positions), order_id,
                )
            # 잔고에서 체결가/수량을 정확히 매칭하기 어려우므로
            # 호출자에게 timeout을 반환하되, 잔고 정보를 포함
            return {
                "status": "timeout",
                "filled_quantity": 0,
                "filled_price": 0,
                "balance_positions": positions,
            }
        except Exception as e:
            logger.error("잔고 폴백 조회 실패: %s", e)
            return {"status": "timeout", "filled_quantity": 0, "filled_price": 0}

    # ═══════════════════════════════════════
    # 부분 체결 처리
    # ═══════════════════════════════════════

    async def handle_partial_fill(self, result: ExecutionResult) -> ExecutionResult:
        """부분 체결 시 잔여 처리"""
        remaining = result.quantity - result.filled_quantity
        if remaining <= 0:
            return result

        fill_ratio = result.filled_quantity / max(result.quantity, 1)

        if fill_ratio >= 0.90 and self.broker:
            # 90% 이상 → 잔여분 시장가 즉시
            logger.info(
                "부분 체결 %.0f%% — 잔여 %d주 시장가",
                fill_ratio * 100, remaining,
            )
            try:
                await self.broker.submit_order({
                    "ticker": result.ticker,
                    "side": result.side.value,
                    "quantity": remaining,
                    "price": None,
                })
            except Exception as e:
                logger.error("잔여분 시장가 주문 실패: %s", e)
        else:
            logger.warning(
                "부분 체결 %.0f%% — 잔여 %d주 미처리",
                fill_ratio * 100, remaining,
            )

        return result

    # ═══════════════════════════════════════
    # 대량 주문 분할
    # ═══════════════════════════════════════

    async def split_large_order(self, order: ApprovedOrder) -> List[ApprovedOrder]:
        """대량 주문 TWAP 분할 (Phase 3에서 고도화)"""
        return [order]

    # ═══════════════════════════════════════
    # 비용 계산
    # ═══════════════════════════════════════

    @staticmethod
    def _calc_commission(amount: float, ticker: str) -> float:
        """수수료 계산"""
        is_overseas = any(kw in ticker for kw in OVERSEAS_KEYWORDS)
        if is_overseas:
            return round(amount * 0.0025, 2)   # 해외 0.25%
        return round(amount * 0.00015, 2)      # 국내 0.015%

    @staticmethod
    def _calc_tax(amount: float, ticker: str, side: OrderSide) -> float:
        """세금 계산 — 매도 시만"""
        if side != OrderSide.SELL:
            return 0
        is_etf = any(kw in ticker for kw in ETF_KEYWORDS)
        if is_etf:
            return 0  # ETF 거래세 면제
        return round(amount * 0.0018, 2)  # 주식 0.18%

    # ═══════════════════════════════════════
    # 시뮬레이션 체결 (브로커 없을 때)
    # ═══════════════════════════════════════

    def _simulate_execution(
        self, order: ApprovedOrder, order_id: str
    ) -> ExecutionResult:
        """브로커 미설정 시 가상 체결"""
        price = order.price or 10_000
        amount = order.quantity * price

        result = ExecutionResult(
            order_id=order_id,
            ticker=order.ticker,
            side=order.side,
            quantity=order.quantity,
            filled_quantity=order.quantity,
            filled_price=price,
            status="simulated",
            commission=self._calc_commission(amount, order.ticker),
            tax=self._calc_tax(amount, order.ticker, order.side),
            slippage_pct=0.0,
            trigger=order.trigger,
        )

        # 모의투자 슬리피지 보정
        if self._slippage_sim:
            result = self._slippage_sim.apply(result, order)

        return result
