"""
RiskService — Layer 5: 리스크 관리 (5중 방어선).

방어선 1: Kill Switch (규칙)      — "숫자가 이상한가?" (0.01초, 인메모리)
방어선 2: LLM 사전검증            — "맥락이 이상한가?" (1~3초, 손절/비상 면제)
방어선 3: 진입 필터               — "들어가도 되는가?" (0.01초)
방어선 4: 실시간 경고 레벨        — "지금 나와야 하는가?" (상시)
방어선 5: 기계적 손절             — "AI 무관하게 나간다" (최후 안전장치)

의존성: DataService, AnalysisService
"""

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Union

from sqlalchemy import text

from app.config import settings
from app.models.contracts import (
    AlertLevel,
    ApprovedOrder,
    EntryFilterResult,
    KillSwitchResult,
    OrderSide,
    OrderTrigger,
    ProposedOrder,
    RejectedOrder,
    ValidationResult,
)
from app.utils import redis_client
from app.utils.db import get_session

logger = logging.getLogger(__name__)

# ── 설정 단축 참조 ──
_risk = settings.risk
_trading = settings.trading


class RiskService:
    """리스크 관리 — 5중 방어선"""

    def __init__(
        self,
        data_service: "DataService",          # noqa: F821
        analysis_service: "AnalysisService",  # noqa: F821
    ) -> None:
        self.data = data_service
        self.analysis = analysis_service

        # 일일 거래 추적
        self._daily_trade_count: int = 0
        self._daily_turnover: float = 0.0
        self._trade_date: date = date.today()

        # 경고 레벨 상태
        self._current_alert: AlertLevel = AlertLevel.NORMAL
        self._last_upgrade_time: Optional[datetime] = None

        # 냉각 기간
        self._cooling_start: Optional[date] = None

        # 포트폴리오 상태 (외부 주입)
        self._portfolio_value: int = 0
        self._positions: Dict[str, dict] = {}
        self._daily_pnl_pct: float = 0.0
        self._weekly_pnl_pct: float = 0.0
        self._monthly_pnl_pct: float = 0.0

        logger.info("RiskService 초기화")

    # ═══════════════════════════════════════
    # 상태 주입
    # ═══════════════════════════════════════

    def update_state(
        self,
        portfolio_value: int,
        positions: Dict[str, dict],
        daily_pnl_pct: float = 0.0,
        weekly_pnl_pct: float = 0.0,
        monthly_pnl_pct: float = 0.0,
    ) -> None:
        """오케스트레이터에서 포트폴리오 상태 갱신"""
        self._portfolio_value = portfolio_value
        self._positions = positions
        self._daily_pnl_pct = daily_pnl_pct
        self._weekly_pnl_pct = weekly_pnl_pct
        self._monthly_pnl_pct = monthly_pnl_pct

        # 날짜 변경 시 일일 카운터 리셋
        if date.today() != self._trade_date:
            self._daily_trade_count = 0
            self._daily_turnover = 0.0
            self._trade_date = date.today()

    # ═══════════════════════════════════════
    # 방어선 1: Kill Switch (규칙, 인메모리)
    # ═══════════════════════════════════════

    def check_kill_switch(self, order: ProposedOrder) -> KillSwitchResult:
        """규칙 기반 Kill Switch — 숫자 이상 즉시 차단"""
        violated: List[str] = []
        details: Dict = {}

        # 1. 냉각 기간 체크 (매수만)
        if self.is_in_cooling_period() and order.side == OrderSide.BUY:
            violated.append(f"냉각 기간 중 — 매수 불가 (종료: {self._cooling_end_date()})")

        # 2. 일일 거래 횟수
        if self._daily_trade_count >= _risk.max_daily_trades:
            violated.append(
                f"일일 거래 횟수 초과: {self._daily_trade_count}/{_risk.max_daily_trades}"
            )
            details["daily_trades"] = self._daily_trade_count

        # 3. 단일 주문 크기
        #    초기 진입(포지션 0개) 시에는 한도를 25%로 완화
        if self._portfolio_value > 0:
            order_pct = order.amount / self._portfolio_value
            is_initial_entry = len(self._positions) == 0
            effective_limit = 0.25 if is_initial_entry else _risk.max_single_order_ratio
            if order_pct > effective_limit:
                violated.append(
                    f"단일 주문 한도 초과: {order_pct:.1%} > {effective_limit:.0%}"
                    + (" (초기진입 완화)" if is_initial_entry else "")
                )
                details["order_pct"] = order_pct

        # 4. 일일 회전율
        if self._portfolio_value > 0:
            projected = self._daily_turnover + order.amount
            turnover_pct = projected / self._portfolio_value
            if turnover_pct > _risk.max_daily_turnover:
                violated.append(
                    f"일일 회전율 초과: {turnover_pct:.1%} > {_risk.max_daily_turnover:.0%}"
                )
                details["turnover_pct"] = turnover_pct

        return KillSwitchResult(
            passed=len(violated) == 0,
            violated_rules=violated,
            details=details,
        )

    # ═══════════════════════════════════════
    # 방어선 2: LLM 사전검증 (AnalysisService 위임)
    # ═══════════════════════════════════════

    async def validate_with_llm(self, order: ProposedOrder) -> ValidationResult:
        """LLM 사전검증 — 맥락적 이상 검사. 손절/비상 면제."""
        # 면제 조건
        exempt = {OrderTrigger.STOP_LOSS, OrderTrigger.EMERGENCY, OrderTrigger.KILL_SWITCH}
        if order.trigger in exempt:
            return ValidationResult(
                decision="approve", confidence=1.0,
                checks={}, size_reduction_pct=100,
                risk_summary="긴급 매도 면제",
            )

        # 시스템 상태 구성
        regime_str = await redis_client.get_regime()
        alert_str = await redis_client.get_alert_level()

        system_state = {
            "regime": regime_str,
            "alert_level": alert_str,
            "portfolio_summary": str(self._positions)[:500],
            "daily_trades": str(self._daily_trade_count),
            "morning_analysis": "",
            "recent_news": "",
            "upcoming_events": "[]",
        }

        return await self.analysis.validate_trade(order, system_state)

    # ═══════════════════════════════════════
    # 방어선 3: 진입 필터
    # ═══════════════════════════════════════

    async def check_entry_filter(self, order: ProposedOrder) -> EntryFilterResult:
        """진입 필터 — 매도는 항상 통과, 매수만 검사"""
        if order.side == OrderSide.SELL:
            return EntryFilterResult(passed=True, reason="매도 면제", details={})

        # 1. 경고 레벨 체크 — 경고/비상 시 신규 매수 차단
        if self._current_alert in (AlertLevel.WARNING, AlertLevel.EMERGENCY):
            return EntryFilterResult(
                passed=False,
                reason=f"경고 레벨 {self._current_alert.value} — 신규 매수 차단",
                details={"alert_level": self._current_alert.value},
            )

        # 2. 단일 종목 집중도
        if self._portfolio_value > 0:
            pos = self._positions.get(order.ticker, {})
            pos_val = float(pos.get("eval_amount", 0) or pos.get("value", 0))
            current_pct = pos_val / self._portfolio_value
            new_pct = current_pct + (order.amount / self._portfolio_value)
            if new_pct > _trading.max_single_stock_weight:
                return EntryFilterResult(
                    passed=False,
                    reason=f"단일 종목 한도 초과: {new_pct:.1%} > {_trading.max_single_stock_weight:.0%}",
                    details={"ticker": order.ticker, "new_pct": new_pct},
                )

        # 3. 매크로 극단값
        try:
            macro = await self.data.get_macro_snapshot()
            if macro:
                if macro.vix > 35:
                    return EntryFilterResult(
                        passed=False,
                        reason=f"VIX {macro.vix:.1f} > 35 — 신규 매수 차단",
                        details={"vix": macro.vix},
                    )
                if macro.hy_spread_percentile > 90:
                    return EntryFilterResult(
                        passed=False,
                        reason=f"HY 스프레드 백분위 {macro.hy_spread_percentile:.0f}% > 90% — 차단",
                        details={"hy_pct": macro.hy_spread_percentile},
                    )
        except Exception as e:
            logger.debug("매크로 데이터 조회 실패 (필터 통과): %s", e)

        return EntryFilterResult(passed=True, reason="통과", details={})

    # ═══════════════════════════════════════
    # 방어선 4: 경고 레벨 평가
    # ═══════════════════════════════════════

    async def evaluate_alert_level(self) -> AlertLevel:
        """실시간 경고 레벨 판단 — 4단계"""
        # 비상 조건 (하나라도 해당)
        emergency_triggers = [
            self._monthly_pnl_pct <= -0.15,
        ]

        # 매크로 데이터 시도
        macro = None
        try:
            macro = await self.data.get_macro_snapshot()
        except Exception:
            pass

        if macro:
            emergency_triggers.extend([
                macro.vix > 35,
                macro.hy_spread_percentile > 90,
            ])

        if any(emergency_triggers):
            return self._apply_hysteresis(AlertLevel.EMERGENCY)

        # 경고 (2개 이상)
        warning_triggers: List[bool] = [
            self._daily_pnl_pct <= -0.10,
        ]
        if macro:
            warning_triggers.extend([
                macro.vix > 25,
                macro.hy_spread_percentile > 75,
                macro.yield_spread < 0,
            ])
        if sum(warning_triggers) >= 2:
            return self._apply_hysteresis(AlertLevel.WARNING)

        # 주의 (2개 이상)
        caution_triggers: List[bool] = [
            self._daily_pnl_pct <= -0.05,
        ]
        if macro:
            caution_triggers.extend([
                macro.vix > 20,
                macro.hy_spread_percentile > 60,
            ])
        if sum(caution_triggers) >= 2:
            return self._apply_hysteresis(AlertLevel.CAUTION)

        return self._apply_hysteresis(AlertLevel.NORMAL)

    def _apply_hysteresis(self, new_level: AlertLevel) -> AlertLevel:
        """히스테리시스 — 상승 즉시, 하락 24시간 후"""
        order_map = {
            AlertLevel.NORMAL: 0,
            AlertLevel.CAUTION: 1,
            AlertLevel.WARNING: 2,
            AlertLevel.EMERGENCY: 3,
        }

        if order_map[new_level] > order_map[self._current_alert]:
            # 위험 상승 → 즉시
            old = self._current_alert
            self._current_alert = new_level
            self._last_upgrade_time = datetime.now()
            logger.warning("경고 레벨 상승: %s → %s", old.value, new_level.value)
        elif order_map[new_level] < order_map[self._current_alert]:
            # 위험 하락 → 24시간 후
            if self._last_upgrade_time:
                hours = (datetime.now() - self._last_upgrade_time).total_seconds() / 3600
                if hours >= 24:
                    self._current_alert = new_level
                    logger.info("경고 레벨 하향: → %s (24시간 경과)", new_level.value)

        return self._current_alert

    # ═══════════════════════════════════════
    # 방어선 5: 기계적 손절
    # ═══════════════════════════════════════

    def check_stop_loss(self, portfolio: dict) -> List[ProposedOrder]:
        """기계적 손절 — AI 판단 무관, 무조건 실행"""
        stop_orders: List[ProposedOrder] = []
        positions = portfolio if portfolio else self._positions

        # 경고 레벨에 따른 손절선
        if self._current_alert in (AlertLevel.CAUTION, AlertLevel.WARNING, AlertLevel.EMERGENCY):
            threshold = _risk.stop_loss_caution   # -5%
        else:
            threshold = _risk.stop_loss_normal    # -7%

        # === 종목 레벨 손절 ===
        for ticker, pos in positions.items():
            pnl_pct = pos.get("pnl_pct", 0)
            # KIS API는 퍼센트 숫자로 반환할 수 있음 (예: -5.2)
            if isinstance(pnl_pct, str):
                try:
                    pnl_pct = float(pnl_pct)
                except (ValueError, TypeError):
                    continue
            # 100 기준이면 소수로 변환
            if abs(pnl_pct) > 1:
                pnl_pct = pnl_pct / 100

            if pnl_pct <= threshold:
                qty = int(pos.get("qty", pos.get("quantity", pos.get("hldg_qty", 0))))
                price = float(pos.get("current_price", pos.get("prpr", 0)))
                if qty > 0:
                    stop_orders.append(ProposedOrder(
                        ticker=ticker,
                        side=OrderSide.SELL,
                        quantity=qty,
                        price=None,  # 시장가
                        amount=int(qty * price) if price else 0,
                        trigger=OrderTrigger.STOP_LOSS,
                        reason=f"기계적 손절: {pnl_pct:.1%} ≤ {threshold:.0%}",
                    ))
                    logger.warning("손절 발동: %s PnL=%.1f%%", ticker, pnl_pct * 100)

        # === 포트폴리오 레벨 ===

        # 일일 한도 -3% → 전 포지션 50% 축소
        if self._daily_pnl_pct <= _risk.portfolio_daily_limit:
            logger.critical("포트폴리오 일일 한도 도달: %.1f%%", self._daily_pnl_pct * 100)
            stop_orders.extend(self._reduce_all_positions(0.50, "일일 한도 도달"))

        # 주간 한도 -5% → 전 포지션 75% 축소
        if self._weekly_pnl_pct <= _risk.portfolio_weekly_limit:
            logger.critical("포트폴리오 주간 한도 도달: %.1f%%", self._weekly_pnl_pct * 100)
            stop_orders.extend(self._reduce_all_positions(0.75, "주간 한도 도달"))

        # 월간 한도 -10% → 전량 현금화 + 30일 냉각
        if self._monthly_pnl_pct <= _risk.portfolio_monthly_limit:
            logger.critical(
                "포트폴리오 월간 한도 도달: %.1f%% — 전량 현금화 + 냉각",
                self._monthly_pnl_pct * 100,
            )
            stop_orders.extend(self._liquidate_all("월간 한도 — 전량 현금화"))
            self._cooling_start = date.today()
            self._save_cooling_period()

        return stop_orders

    def _reduce_all_positions(self, reduce_pct: float, reason: str) -> List[ProposedOrder]:
        """전 포지션 N% 축소"""
        orders: List[ProposedOrder] = []
        for ticker, pos in self._positions.items():
            qty = int(pos.get("qty", pos.get("quantity", 0)))
            price = float(pos.get("current_price", 0))
            sell_qty = int(qty * reduce_pct)
            if sell_qty > 0:
                orders.append(ProposedOrder(
                    ticker=ticker, side=OrderSide.SELL, quantity=sell_qty,
                    price=None, amount=int(sell_qty * price),
                    trigger=OrderTrigger.EMERGENCY, reason=reason,
                ))
        return orders

    def _liquidate_all(self, reason: str) -> List[ProposedOrder]:
        """전량 현금화"""
        return self._reduce_all_positions(1.0, reason)

    # ═══════════════════════════════════════
    # 메인: 5중 방어선 통과
    # ═══════════════════════════════════════

    async def process_order(
        self, order: ProposedOrder
    ) -> Union[ApprovedOrder, RejectedOrder]:
        """ProposedOrder → 5중 방어선 → ApprovedOrder 또는 RejectedOrder"""

        # 1층: Kill Switch
        ks = self.check_kill_switch(order)
        if not ks.passed:
            reason = "; ".join(ks.violated_rules)
            logger.warning("Kill Switch 차단: %s", reason)
            await self._log_stop_event("kill_switch", order.ticker, 0, reason)
            return RejectedOrder(
                original_order=order, rejected_by="kill_switch", reason=reason,
            )

        # 2층: LLM 사전검증 (손절/비상 면제)
        exempt = {OrderTrigger.STOP_LOSS, OrderTrigger.EMERGENCY, OrderTrigger.KILL_SWITCH}
        if order.trigger not in exempt:
            try:
                val = await self.validate_with_llm(order)
                if val.decision == "reject":
                    logger.warning("LLM 검증 거부: %s", val.risk_summary)
                    return RejectedOrder(
                        original_order=order,
                        rejected_by="llm_validation",
                        reason=val.risk_summary,
                    )
                if val.decision == "defer":
                    logger.info("LLM 검증 보류: %s", val.risk_summary)
                    return RejectedOrder(
                        original_order=order,
                        rejected_by="llm_defer",
                        reason=val.risk_summary,
                    )
                if val.decision == "conditional_approve" and val.size_reduction_pct < 100:
                    original_qty = order.quantity
                    original_amt = order.amount
                    ratio = val.size_reduction_pct / 100
                    order.quantity = max(1, int(order.quantity * ratio))
                    order.amount = int(order.amount * ratio)
                    logger.info(
                        "LLM 조건부 승인: %s 수량 %d→%d (%d%%)",
                        order.ticker, original_qty, order.quantity, val.size_reduction_pct,
                    )
            except Exception as e:
                if self._is_inverse_conflict(order):
                    logger.warning(
                        "LLM 검증 타임아웃 + 인버스 충돌 감지 → reject: %s (%s)", order.ticker, e,
                    )
                    return RejectedOrder(
                        original_order=order,
                        rejected_by="llm_timeout_inverse",
                        reason=f"LLM 검증 타임아웃 — 롱+인버스 동시 보유 위험으로 거부: {e}",
                    )
                logger.error("LLM 검증 에러 (통과 처리): %s", e)

        # 3층: 진입 필터
        ef = await self.check_entry_filter(order)
        if not ef.passed:
            logger.warning("진입 필터 차단: %s", ef.reason)
            return RejectedOrder(
                original_order=order, rejected_by="entry_filter", reason=ef.reason,
            )

        # 모두 통과 → 승인
        self._daily_trade_count += 1
        self._daily_turnover += order.amount

        approved = ApprovedOrder.from_proposed(order)
        return approved

    # ═══════════════════════════════════════
    # 보조 메서드
    # ═══════════════════════════════════════

    # 인버스 ETF 키워드
    _INVERSE_KEYWORDS = ("인버스", "inverse", "숏", "short", "곱버스")

    def _is_inverse_conflict(self, order: ProposedOrder) -> bool:
        """주문 또는 기존 포지션에 인버스 종목이 포함되어 롱+숏 충돌 위험이 있는지 판단."""
        order_is_inverse = any(kw in order.ticker.lower() for kw in self._INVERSE_KEYWORDS)
        portfolio_has_inverse = any(
            any(kw in ticker.lower() for kw in self._INVERSE_KEYWORDS)
            for ticker in self._positions
        )
        portfolio_has_long = any(
            not any(kw in ticker.lower() for kw in self._INVERSE_KEYWORDS)
            for ticker in self._positions
        )

        # 인버스 주문 + 포트폴리오에 롱 보유, 또는 롱 주문 + 포트폴리오에 인버스 보유
        if order_is_inverse and portfolio_has_long:
            return True
        if not order_is_inverse and portfolio_has_inverse:
            return True
        return False

    def get_position_adjustment(self) -> float:
        """경고 레벨별 포지션 조정 비율"""
        return {
            AlertLevel.NORMAL: 1.0,
            AlertLevel.CAUTION: 0.85,
            AlertLevel.WARNING: 0.50,
            AlertLevel.EMERGENCY: 0.10,
        }.get(self._current_alert, 1.0)

    def is_in_cooling_period(self) -> bool:
        """냉각 기간 여부"""
        if self._cooling_start:
            days_passed = (date.today() - self._cooling_start).days
            if days_passed < _risk.cooling_period_days:
                return True
            self._cooling_start = None
        return False

    def _cooling_end_date(self) -> str:
        if self._cooling_start:
            return (self._cooling_start + timedelta(days=_risk.cooling_period_days)).isoformat()
        return "N/A"

    async def _log_stop_event(
        self, event_type: str, ticker: str, loss_pct: float, reason: str
    ) -> None:
        """stop_loss_events 테이블에 기록"""
        try:
            async with get_session() as session:
                session.execute(
                    text("""
                        INSERT INTO stop_loss_events
                            (event_type, ticker, loss_pct, reason)
                        VALUES (:etype, :ticker, :loss, :reason)
                    """),
                    {"etype": event_type, "ticker": ticker, "loss": loss_pct, "reason": reason},
                )
        except Exception as e:
            logger.error("stop_loss_events 저장 실패: %s", e)

    def _save_cooling_period(self) -> None:
        """냉각 기간 DB 기록 (동기 저장은 스킵, 오케스트레이터에서 처리)"""
        logger.info(
            "냉각 기간 시작: %s ~ %s",
            self._cooling_start,
            self._cooling_end_date(),
        )


