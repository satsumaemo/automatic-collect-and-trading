"""
PortfolioService — Layer 4: 포트폴리오 관리 (동적 자산배분).

자산배분 → 섹터 ETF 선택 → 포지션 사이징 → 리밸런싱 주문 생성.
오케스트레이터가 update_portfolio_state()로 상태를 주입합니다.

의존성: SignalService, config.settings
"""

import logging
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text

import math

from app.config import settings
from app.models.contracts import (
    ConvergenceResult,
    ConvergenceType,
    OrderSide,
    OrderTrigger,
    ProposedOrder,
    Regime,
    TradingSignals,
    REGIME_ALLOCATION,
    SECTOR_ETF_MAPPING,
)
from app.utils.db import get_session

logger = logging.getLogger(__name__)

# Kill Switch 단일 주문 한도 (분할 기준)
MAX_SINGLE_ORDER_PCT = settings.risk.max_single_order_ratio  # 0.10

# ── 자산군 정의 ──
ASSET_CLASSES = {
    "kr_equity": {
        "description": "국내 주식",
        "default_etf": "KODEX 200",
        "min_pct": 0,
        "max_pct": 60,
    },
    "us_equity": {
        "description": "미국 주식",
        "default_etf": "TIGER 미국S&P500",
        "min_pct": 0,
        "max_pct": 50,
    },
    "kr_bond": {
        "description": "국내 채권",
        "default_etf": "KODEX 국고채10년",
        "alternatives": {
            "short": "TIGER 단기채권",
            "medium": "KODEX 종합채권",
            "long": "KODEX 국고채10년",
        },
        "min_pct": 0,
        "max_pct": 40,
    },
    "us_bond": {
        "description": "미국 채권",
        "default_etf": "TIGER 미국채10년선물",
        "alternatives": {
            "short": "TIGER 미국채10년선물",
            "medium": "TIGER 미국채10년선물",
            "long": "ACE 미국30년국채",
        },
        "min_pct": 0,
        "max_pct": 30,
    },
    "gold": {
        "description": "금",
        "default_etf": "KODEX 골드선물(H)",
        "min_pct": 0,
        "max_pct": 15,
    },
    "cash_rp": {
        "description": "현금/RP",
        "min_pct": 5,
        "max_pct": 50,
    },
}

# 편의 상수
MIN_TRADE = settings.trading.min_trade_amount
MAX_POSITION = settings.trading.max_single_stock_weight
MAX_SECTOR = settings.trading.max_single_sector_weight
MIN_CASH = settings.trading.min_cash_ratio
REBAL_INTERVAL = settings.trading.rebalance_min_interval_days
DRIFT_THRESHOLD = settings.trading.rebalance_drift_threshold


class PortfolioService:
    """포트폴리오 배분 및 주문 생성"""

    def __init__(self, signal_service: "SignalService") -> None:  # noqa: F821
        self.signals = signal_service

        # 포트폴리오 상태 (오케스트레이터에서 주입)
        self._portfolio_value: int = 0
        self._current_positions: Dict[str, dict] = {}
        self._current_allocation: Dict[str, float] = {}
        self._last_rebalance_date: Optional[date] = None

        logger.info("PortfolioService 초기화")

    # ═══════════════════════════════════════
    # 상태 주입
    # ═══════════════════════════════════════

    def update_portfolio_state(
        self,
        total_value: int,
        positions: Dict[str, dict],
        allocation: Dict[str, float],
    ) -> None:
        """오케스트레이터가 매일 호출하여 상태 주입"""
        self._portfolio_value = total_value
        self._current_positions = positions
        self._current_allocation = allocation
        logger.info(
            "포트폴리오 상태 갱신: 총자산=%s원, 포지션=%d개",
            f"{total_value:,}", len(positions),
        )

    def record_rebalance(self) -> None:
        """리밸런싱 실행 후 날짜 기록"""
        self._last_rebalance_date = date.today()

    # ═══════════════════════════════════════
    # 목표 배분 계산
    # ═══════════════════════════════════════

    def calculate_target_allocation(self, regime: Regime) -> Dict[str, float]:
        """레짐 기반 목표 자산배분 (%). bond_duration 제외."""
        allocation = REGIME_ALLOCATION.get(regime, REGIME_ALLOCATION[Regime.SLOWDOWN])
        return {k: v for k, v in allocation.items() if k != "bond_duration"}

    def select_bond_etf(self, regime: Regime) -> Dict[str, str]:
        """레짐의 bond_duration에 따라 채권 ETF 선택"""
        allocation = REGIME_ALLOCATION.get(regime, {})
        duration = allocation.get("bond_duration", "medium")
        bond_map = {
            "short": {"kr": "TIGER 단기채권", "us": "TIGER 미국채10년선물"},
            "medium": {"kr": "KODEX 종합채권", "us": "TIGER 미국채10년선물"},
            "long": {"kr": "KODEX 국고채10년", "us": "ACE 미국30년국채"},
        }
        return bond_map.get(duration, bond_map["medium"])

    # ═══════════════════════════════════════
    # 섹터 ETF 선택
    # ═══════════════════════════════════════

    def select_sector_etfs(
        self,
        convergence: List[ConvergenceResult],
        total_equity_amount: int,
    ) -> Dict[str, int]:
        """
        수렴 기반 섹터 ETF 선택.
        수렴 섹터 → 주식 배분의 최대 60%
        나머지 → 지수추종 ETF (최소 40%)
        반환: {ETF명: 배분금액(원)}
        """
        allocations: Dict[str, int] = {}

        # 1. 수렴 섹터에 배분
        converged = [
            r for r in convergence
            if r.convergence_type in (ConvergenceType.STRONG, ConvergenceType.WEAK)
        ]

        if converged:
            converged_budget = int(total_equity_amount * 0.60)
            total_conf = sum(r.confidence for r in converged)

            if total_conf > 0:
                for result in converged:
                    weight = result.confidence / total_conf
                    amount = int(converged_budget * weight)
                    etf_count = max(len(result.recommended_etfs), 1)
                    for etf in result.recommended_etfs:
                        per_etf = amount // etf_count
                        if per_etf >= MIN_TRADE:
                            allocations[etf] = allocations.get(etf, 0) + per_etf

        # 2. 나머지는 지수추종 ETF
        allocated = sum(allocations.values())
        index_budget = total_equity_amount - allocated

        if index_budget >= MIN_TRADE:
            kr_share = int(index_budget * 0.4)
            us_share = index_budget - kr_share
            if kr_share >= MIN_TRADE:
                allocations["KODEX 200"] = allocations.get("KODEX 200", 0) + kr_share
            if us_share >= MIN_TRADE:
                allocations["TIGER 미국S&P500"] = allocations.get("TIGER 미국S&P500", 0) + us_share

        return allocations

    # ═══════════════════════════════════════
    # 포지션 사이징
    # ═══════════════════════════════════════

    def calculate_position_sizes(self, signals: TradingSignals) -> Dict[str, int]:
        """확신도 × 레짐 × 배율 → 포지션 크기 (원)"""
        sizes: Dict[str, int] = {}
        base_pct = 0.10  # 기본: 전체 자산의 10%

        regime_mult = {
            Regime.EXPANSION: 1.0,
            Regime.SLOWDOWN: 0.7,
            Regime.WARNING: 0.3,
            Regime.CRISIS: 0.1,
        }.get(signals.regime.regime, 0.7)

        for result in signals.convergence_results:
            if result.convergence_type not in (ConvergenceType.STRONG, ConvergenceType.WEAK):
                continue

            raw_pct = base_pct * result.confidence * regime_mult * result.position_multiplier
            final_pct = min(raw_pct, MAX_POSITION)
            amount = int(self._portfolio_value * final_pct)

            etf_count = max(len(result.recommended_etfs), 1)
            for etf in result.recommended_etfs:
                per_etf = amount // etf_count
                if per_etf >= MIN_TRADE:
                    sizes[etf] = sizes.get(etf, 0) + per_etf

        return sizes

    # ═══════════════════════════════════════
    # 리밸런싱 판단
    # ═══════════════════════════════════════

    def check_rebalance_needed(self) -> Tuple[bool, List[ProposedOrder]]:
        """
        현재 배분 vs 목표 배분 비교.
        드리프트 임계(5%p) 초과 시 리밸런싱 주문 생성.
        """
        # 간격 체크
        if self._last_rebalance_date:
            days_since = (date.today() - self._last_rebalance_date).days
            if days_since < REBAL_INTERVAL:
                logger.info(
                    "리밸런싱 간격 미달: %d일/%d일", days_since, REBAL_INTERVAL
                )
                return False, []

        if self._portfolio_value <= 0:
            logger.warning("포트폴리오 가치 0 — 리밸런싱 스킵")
            return False, []

        # 목표 배분
        try:
            regime = Regime(getattr(self.signals, "_last_regime", "slowdown"))
        except ValueError:
            regime = Regime.SLOWDOWN

        target = self.calculate_target_allocation(regime)
        bond_etfs = self.select_bond_etf(regime)

        needs_rebalance = False
        orders: List[ProposedOrder] = []

        for asset_class, target_pct in target.items():
            if asset_class == "cash_rp":
                continue  # 현금은 주문 대상 아님

            current_pct = self._current_allocation.get(asset_class, 0.0)
            drift = abs(current_pct - target_pct)

            if drift < DRIFT_THRESHOLD * 100:
                # DRIFT_THRESHOLD는 0.05 (5%p) — target_pct는 % 단위
                continue

            needs_rebalance = True
            trade_pct = target_pct - current_pct  # 양수=매수, 음수=매도
            trade_amount = int(self._portfolio_value * abs(trade_pct) / 100)

            if trade_amount < MIN_TRADE:
                continue

            # 자산군 → ETF 매핑
            if asset_class == "kr_bond":
                etf_ticker = bond_etfs["kr"]
            elif asset_class == "us_bond":
                etf_ticker = bond_etfs["us"]
            else:
                ac_info = ASSET_CLASSES.get(asset_class, {})
                etf_ticker = ac_info.get("default_etf")

            if not etf_ticker:
                continue

            orders.append(ProposedOrder(
                ticker=etf_ticker,
                side=OrderSide.BUY if trade_pct > 0 else OrderSide.SELL,
                quantity=0,  # 금액 기반 — 실행 시 가격으로 계산
                price=None,
                amount=trade_amount,
                trigger=OrderTrigger.REBALANCE,
                reason=(
                    f"리밸런싱: {asset_class} "
                    f"{current_pct:.1f}%→{target_pct:.1f}% "
                    f"(드리프트 {drift:.1f}%p)"
                ),
                sector=asset_class,
            ))

        # 매도 먼저, 매수 나중에 (현금 확보 후 매수)
        orders.sort(key=lambda o: 0 if o.side == OrderSide.SELL else 1)
        return needs_rebalance, orders

    # ═══════════════════════════════════════
    # 최종 주문 생성
    # ═══════════════════════════════════════

    def generate_orders(self) -> List[ProposedOrder]:
        """최종 주문 목록 생성. 리밸런싱 주문 + 집중도 제한 + 대량 주문 분할."""
        # 1. 리밸런싱 체크
        needs_rebalance, orders = self.check_rebalance_needed()
        if needs_rebalance:
            logger.info("리밸런싱 주문 %d건 생성", len(orders))

        # 2. 집중도 제한 적용
        orders = self._apply_concentration_limits(orders)

        # 3. 최소 현금 비율 확보 확인
        orders = self._ensure_min_cash(orders)

        # 4. Kill Switch 단일 주문 한도 초과 시 자동 분할
        orders = self._split_large_orders(orders)

        return orders

    def _split_large_orders(
        self, orders: List[ProposedOrder]
    ) -> List[ProposedOrder]:
        """
        단일 주문이 MAX_SINGLE_ORDER_PCT(10%)를 초과하면 자동 분할.
        예: 20% 주문 → 10% + 10% 두 건.
        """
        if self._portfolio_value <= 0:
            return orders

        max_amount = int(self._portfolio_value * MAX_SINGLE_ORDER_PCT)
        if max_amount <= 0:
            return orders

        result: List[ProposedOrder] = []
        for order in orders:
            if order.amount <= max_amount:
                result.append(order)
                continue

            # 분할 필요
            n_splits = math.ceil(order.amount / max_amount)
            base_amount = order.amount // n_splits
            remainder = order.amount - base_amount * n_splits

            logger.info(
                "주문 분할: %s %s %s원 → %d건 (각 %s원)",
                order.ticker, order.side.value,
                f"{order.amount:,}", n_splits, f"{base_amount:,}",
            )

            for i in range(n_splits):
                split_amount = base_amount + (1 if i < remainder else 0)
                if split_amount < MIN_TRADE:
                    continue
                result.append(ProposedOrder(
                    ticker=order.ticker,
                    side=order.side,
                    quantity=order.quantity,
                    price=order.price,
                    amount=split_amount,
                    trigger=order.trigger,
                    reason=f"{order.reason} (분할 {i+1}/{n_splits})",
                    sector=order.sector,
                ))

        return result

    def _apply_concentration_limits(
        self, orders: List[ProposedOrder]
    ) -> List[ProposedOrder]:
        """집중도 제한 — 단일 종목 20%, 단일 섹터 35% 초과 방지"""
        if self._portfolio_value <= 0:
            return orders

        filtered: List[ProposedOrder] = []

        for order in orders:
            if order.side == OrderSide.SELL:
                filtered.append(order)
                continue

            order_pct = order.amount / self._portfolio_value

            # 단일 종목 한도
            current_pct = self._get_position_pct(order.ticker)
            if current_pct + order_pct > MAX_POSITION:
                max_amount = int((MAX_POSITION - current_pct) * self._portfolio_value)
                if max_amount < MIN_TRADE:
                    logger.warning("집중도 제한으로 주문 제거: %s", order.ticker)
                    continue
                order.amount = max_amount
                order.reason += " (종목 집중도 제한으로 축소)"

            # 단일 섹터 한도
            if order.sector:
                sector_pct = self._get_sector_pct(order.sector)
                if sector_pct + order_pct > MAX_SECTOR:
                    max_amount = int((MAX_SECTOR - sector_pct) * self._portfolio_value)
                    if max_amount < MIN_TRADE:
                        logger.warning(
                            "섹터 제한으로 주문 제거: %s (%s)", order.ticker, order.sector
                        )
                        continue
                    order.amount = max_amount
                    order.reason += " (섹터 제한으로 축소)"

            filtered.append(order)

        return filtered

    def _ensure_min_cash(self, orders: List[ProposedOrder]) -> List[ProposedOrder]:
        """매수 주문 후에도 최소 현금 비율(5%)이 유지되는지 확인"""
        if self._portfolio_value <= 0:
            return orders

        # 현재 현금 추정 (배분 정보 없으면 포지션 합산에서 역산)
        if self._current_allocation:
            cash_pct = self._current_allocation.get("cash_rp", 0) / 100
        else:
            # 포지션 합산 → 나머지가 현금
            pos_total = sum(
                float(p.get("eval_amount", 0) or p.get("value", 0))
                for p in self._current_positions.values()
            )
            cash_pct = max(0, 1.0 - pos_total / self._portfolio_value) if self._portfolio_value > 0 else 1.0
        current_cash = self._portfolio_value * cash_pct

        # 매수 총액 계산
        total_buy = sum(o.amount for o in orders if o.side == OrderSide.BUY)
        total_sell = sum(o.amount for o in orders if o.side == OrderSide.SELL)
        projected_cash = current_cash - total_buy + total_sell

        min_cash_amount = self._portfolio_value * MIN_CASH

        if projected_cash >= min_cash_amount:
            return orders

        # 현금 부족 → 매수 주문을 비례 축소
        shortage = min_cash_amount - projected_cash
        if total_buy <= 0:
            return orders

        reduction_ratio = max(0, 1 - shortage / total_buy)
        logger.warning(
            "최소 현금 비율 확보를 위해 매수 주문 %.0f%% 축소",
            (1 - reduction_ratio) * 100,
        )

        adjusted: List[ProposedOrder] = []
        for order in orders:
            if order.side == OrderSide.BUY:
                new_amount = int(order.amount * reduction_ratio)
                if new_amount >= MIN_TRADE:
                    order.amount = new_amount
                    order.reason += f" (현금 확보 위해 {reduction_ratio:.0%}로 축소)"
                    adjusted.append(order)
            else:
                adjusted.append(order)

        return adjusted

    # ── 내부 헬퍼 ──

    def _get_position_pct(self, ticker: str) -> float:
        """현재 포지션 비중 (0~1)"""
        pos = self._current_positions.get(ticker, {})
        val = pos.get("eval_amount", 0) or pos.get("value", 0)
        if self._portfolio_value <= 0:
            return 0
        return val / self._portfolio_value

    def _get_sector_pct(self, sector: str) -> float:
        """현재 섹터 합산 비중 (0~1)"""
        return self._current_allocation.get(sector, 0) / 100

    async def save_portfolio_targets(self, regime: Regime) -> None:
        """portfolio_targets 테이블에 오늘 목표 저장"""
        target = self.calculate_target_allocation(regime)
        allocation = REGIME_ALLOCATION.get(regime, {})
        today = date.today()

        try:
            async with get_session() as session:
                session.execute(
                    text("""
                        INSERT INTO portfolio_targets
                            (date, regime, kr_equity_pct, us_equity_pct,
                             kr_bond_pct, us_bond_pct, gold_pct, cash_rp_pct,
                             bond_duration)
                        VALUES
                            (:dt, :regime, :kr_eq, :us_eq,
                             :kr_bd, :us_bd, :gold, :cash,
                             :dur)
                        ON CONFLICT (date) DO UPDATE SET
                            regime = EXCLUDED.regime,
                            kr_equity_pct = EXCLUDED.kr_equity_pct,
                            us_equity_pct = EXCLUDED.us_equity_pct,
                            kr_bond_pct = EXCLUDED.kr_bond_pct,
                            us_bond_pct = EXCLUDED.us_bond_pct,
                            gold_pct = EXCLUDED.gold_pct,
                            cash_rp_pct = EXCLUDED.cash_rp_pct,
                            bond_duration = EXCLUDED.bond_duration
                    """),
                    {
                        "dt": today,
                        "regime": regime.value,
                        "kr_eq": target.get("kr_equity", 0),
                        "us_eq": target.get("us_equity", 0),
                        "kr_bd": target.get("kr_bond", 0),
                        "us_bd": target.get("us_bond", 0),
                        "gold": target.get("gold", 0),
                        "cash": target.get("cash_rp", 0),
                        "dur": allocation.get("bond_duration", "medium"),
                    },
                )
        except Exception as e:
            logger.error("portfolio_targets 저장 실패: %s", e)
