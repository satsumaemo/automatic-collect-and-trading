"""
MonitoringService — Layer 7: 모니터링, 알림, 성과 추적.

모든 서비스의 이벤트를 수신하여 DB 기록 + 텔레그램 알림 + 성과 계산을 수행합니다.
기록 실패가 트레이딩을 멈추지 않도록 모든 DB 작업을 try/except로 감쌉니다.

의존성: 이벤트 기반 (다른 서비스에서 호출)
"""

import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List

from sqlalchemy import text

from app.models.contracts import (
    AlertLevel,
    DailyReport,
    ExecutionResult,
    Regime,
    RegimeAnalysis,
    RejectedOrder,
    ValidationResult,
)
from app.utils import redis_client
from app.utils.db import get_session
from app.utils.telegram import notifier

logger = logging.getLogger(__name__)


class MonitoringService:
    """이벤트 수신, 성과 측정, 알림 발송"""

    def __init__(self) -> None:
        self._today_trades: List[ExecutionResult] = []
        self._today_rejections: List[RejectedOrder] = []
        logger.info("MonitoringService 초기화")

    # ═══════════════════════════════════════
    # 거래 체결 이벤트
    # ═══════════════════════════════════════

    async def on_trade_executed(self, result: ExecutionResult) -> None:
        """거래 체결 → trade_history + orders DB 기록 + 텔레그램 알림"""
        self._today_trades.append(result)

        # trade_history 기록
        try:
            async with get_session() as session:
                session.execute(
                    text("""
                        INSERT INTO trade_history
                            (date, ticker, side, quantity, price, amount,
                             commission, tax, trigger, pnl, pnl_pct, holding_days)
                        VALUES
                            (:dt, :ticker, :side, :qty, :price, :amount,
                             :comm, :tax, :trigger, 0, 0, 0)
                    """),
                    {
                        "dt": date.today(),
                        "ticker": result.ticker,
                        "side": result.side.value,
                        "qty": result.filled_quantity,
                        "price": result.filled_price,
                        "amount": int(result.filled_quantity * result.filled_price),
                        "comm": result.commission,
                        "tax": result.tax,
                        "trigger": result.trigger.value,
                    },
                )
        except Exception as e:
            logger.error("trade_history 저장 실패: %s", e)

        # orders 기록
        try:
            async with get_session() as session:
                session.execute(
                    text("""
                        INSERT INTO orders
                            (order_id, broker, market, ticker, side, order_type,
                             quantity, price, status, filled_quantity, filled_price,
                             slippage_pct, commission, tax, trigger_source)
                        VALUES
                            (:oid, 'KIS', 'KR', :ticker, :side, 'market',
                             :qty, :price, :status, :fqty, :fprice,
                             :slip, :comm, :tax, :trigger)
                        ON CONFLICT (order_id) DO NOTHING
                    """),
                    {
                        "oid": result.order_id,
                        "ticker": result.ticker,
                        "side": result.side.value,
                        "qty": result.quantity,
                        "price": result.filled_price,
                        "status": result.status,
                        "fqty": result.filled_quantity,
                        "fprice": result.filled_price,
                        "slip": result.slippage_pct,
                        "comm": result.commission,
                        "tax": result.tax,
                        "trigger": result.trigger.value,
                    },
                )
        except Exception as e:
            logger.error("orders 저장 실패: %s", e)

        # 텔레그램 알림
        try:
            await notifier.trade_executed({
                "side": result.side.value,
                "ticker": result.ticker,
                "quantity": result.filled_quantity,
                "price": result.filled_price,
                "trigger": result.trigger.value,
            })
        except Exception as e:
            logger.error("거래 알림 전송 실패: %s", e)

    # ═══════════════════════════════════════
    # 주문 거부 이벤트
    # ═══════════════════════════════════════

    async def on_order_rejected(self, order: RejectedOrder) -> None:
        """주문 거부 → 로깅 + DB 기록"""
        self._today_rejections.append(order)
        logger.warning(
            "주문 거부: %s by %s — %s",
            order.original_order.ticker, order.rejected_by, order.reason,
        )

        try:
            async with get_session() as session:
                oid = f"REJ-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
                session.execute(
                    text("""
                        INSERT INTO orders
                            (order_id, broker, market, ticker, side, quantity,
                             price, status, trigger_source, error_message)
                        VALUES
                            (:oid, 'KIS', 'KR', :ticker, :side, :qty,
                             :price, 'rejected', :trigger, :reason)
                    """),
                    {
                        "oid": oid,
                        "ticker": order.original_order.ticker,
                        "side": order.original_order.side.value,
                        "qty": order.original_order.quantity,
                        "price": order.original_order.price,
                        "trigger": order.original_order.trigger.value,
                        "reason": f"[{order.rejected_by}] {order.reason}"[:500],
                    },
                )
        except Exception as e:
            logger.error("거부 기록 저장 실패: %s", e)

    # ═══════════════════════════════════════
    # 상태 변경 이벤트
    # ═══════════════════════════════════════

    async def on_alert_level_changed(
        self, old: AlertLevel, new: AlertLevel
    ) -> None:
        """경고 레벨 변경 → DB + 텔레그램"""
        logger.warning("경고 레벨: %s → %s", old.value, new.value)

        try:
            async with get_session() as session:
                session.execute(
                    text("""
                        INSERT INTO alert_level_history
                            (timestamp, alert_level, triggers, actions_taken)
                        VALUES (NOW(), :level, :triggers, :actions)
                    """),
                    {
                        "level": new.value,
                        "triggers": [f"{old.value}→{new.value}"],
                        "actions": ["level_change"],
                    },
                )
        except Exception as e:
            logger.error("경고 레벨 기록 실패: %s", e)

        try:
            await notifier.alert_level_change(old.value, new.value)
        except Exception as e:
            logger.error("경고 알림 전송 실패: %s", e)

    async def on_regime_changed(self, old: Regime, new: Regime) -> None:
        """시장 레짐 변경 → 텔레그램"""
        old_val = old.value if hasattr(old, "value") else str(old)
        new_val = new.value if hasattr(new, "value") else str(new)
        logger.info("레짐: %s → %s", old_val, new_val)

        try:
            await notifier.llm_analysis_complete(
                "regime_change", f"{old_val} → {new_val}"
            )
        except Exception as e:
            logger.error("레짐 알림 전송 실패: %s", e)

    async def on_llm_validation(self, result: ValidationResult) -> None:
        """LLM 검증 결과 기록"""
        logger.info(
            "LLM 검증: %s (확신도: %.2f)", result.decision, result.confidence
        )

    # ═══════════════════════════════════════
    # 일일 성과
    # ═══════════════════════════════════════

    def calculate_daily_performance(self) -> DailyReport:
        """일일 성과 계산"""
        return DailyReport(
            date=datetime.now(),
            portfolio_value=0,
            daily_return=0.0,
            cumulative_return=0.0,
            current_drawdown=0.0,
            max_drawdown=0.0,
            sharpe_ratio=None,
            equity_pct=0.0,
            bond_pct=0.0,
            gold_pct=0.0,
            cash_pct=100.0,
            trade_count=len(self._today_trades),
            regime=Regime.SLOWDOWN,
            alert_level=AlertLevel.NORMAL,
        )

    async def send_daily_report(self) -> None:
        """매일 16:30 일일 리포트 전송"""
        report = self.calculate_daily_performance()

        # daily_performance DB 저장
        try:
            regime_str = await redis_client.get_regime()
            alert_str = await redis_client.get_alert_level()
            total_comm = sum(t.commission + t.tax for t in self._today_trades)

            async with get_session() as session:
                session.execute(
                    text("""
                        INSERT INTO daily_performance
                            (date, portfolio_value, daily_return, cumulative_return,
                             drawdown, max_drawdown, trade_count, total_commission,
                             regime, alert_level)
                        VALUES
                            (:dt, :pv, :dr, :cr, :dd, :mdd, :tc, :comm,
                             :regime, :alert)
                        ON CONFLICT (date) DO UPDATE SET
                            portfolio_value = EXCLUDED.portfolio_value,
                            daily_return = EXCLUDED.daily_return,
                            trade_count = EXCLUDED.trade_count,
                            total_commission = EXCLUDED.total_commission
                    """),
                    {
                        "dt": date.today(),
                        "pv": report.portfolio_value,
                        "dr": report.daily_return,
                        "cr": report.cumulative_return,
                        "dd": report.current_drawdown,
                        "mdd": report.max_drawdown,
                        "tc": report.trade_count,
                        "comm": total_comm,
                        "regime": regime_str,
                        "alert": alert_str,
                    },
                )
        except Exception as e:
            logger.error("일일 성과 DB 저장 실패: %s", e)

        # 텔레그램 발송
        try:
            await notifier.daily_summary({
                "portfolio_value": report.portfolio_value,
                "daily_return": report.daily_return,
                "cumulative_return": report.cumulative_return,
                "max_drawdown": report.max_drawdown,
                "regime": report.regime.value,
                "alert_level": report.alert_level.value,
            })
        except Exception as e:
            logger.error("일일 리포트 전송 실패: %s", e)

        # 일일 카운터 리셋
        self._today_trades = []
        self._today_rejections = []
        logger.info("일일 리포트 전송 완료")

    def log_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """범용 이벤트 로깅"""
        logger.info(
            "[%s] %s",
            event_type,
            json.dumps(data, ensure_ascii=False, default=str)[:500],
        )
