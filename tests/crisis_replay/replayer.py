"""
위기 리플레이 엔진.
과거 위기 상황을 재현하여 시스템 반응(레짐/경고/매수차단)을 검증합니다.
"""

import json
import logging
from datetime import date, datetime

from sqlalchemy import text

from app.models.contracts import (
    AlertLevel,
    MacroSnapshot,
    OrderSide,
    OrderTrigger,
    ProposedOrder,
    ApprovedOrder,
)
from app.utils import redis_client
from app.utils.db import get_session
from tests.crisis_replay.scenarios import HISTORICAL_CRISES
from tests.fault_injection.injector import FaultInjector

logger = logging.getLogger(__name__)


class CrisisReplayer:
    """과거 위기 리플레이"""

    def __init__(self, risk_service, analysis_service, data_service) -> None:
        self.risk = risk_service
        self.analysis = analysis_service
        self.data = data_service
        self.injector = FaultInjector()

    async def replay(self, crisis_name: str) -> dict:
        """
        단일 위기 시나리오 리플레이.
        반환: {"crisis", "regime_match", "alert_match", "buy_blocked_correctly", "all_passed"}
        """
        scenario = HISTORICAL_CRISES.get(crisis_name)
        if not scenario:
            return {"crisis": crisis_name, "all_passed": False, "error": "시나리오 없음"}

        expected = scenario["expected"]
        result = {
            "crisis": crisis_name,
            "name": scenario["name"],
            "regime_match": False,
            "alert_match": False,
            "buy_blocked_correctly": False,
            "all_passed": False,
        }

        try:
            # 1. 시장 데이터 주입
            ms = scenario["market_state"]
            await self.injector.setup_scenario({
                "setup": {
                    "force_regime": expected.get("regime", "slowdown"),
                    "alert_level": expected.get("alert_level", "normal"),
                }
            })

            # RiskService에 매크로 상태 주입
            self.risk.update_state(
                portfolio_value=100_000_000,
                positions={},
                daily_pnl_pct=0.0,
                weekly_pnl_pct=0.0,
                monthly_pnl_pct=0.0,
            )
            # VIX 기반 경고 레벨 직접 설정
            if ms.get("vix", 0) > 35:
                self.risk._current_alert = AlertLevel.EMERGENCY
            elif ms.get("vix", 0) > 25:
                self.risk._current_alert = AlertLevel.WARNING

            # 2. 경고 레벨 평가
            alert = self.risk.evaluate_alert_level()
            result["alert_match"] = alert.value == expected["alert_level"]

            # 3. 레짐 확인 (Redis에서)
            regime_str = await redis_client.get_regime()
            result["regime_match"] = regime_str == expected["regime"]

            # 4. 테스트 매수 주문
            test_order = ProposedOrder(
                ticker="KODEX 200",
                side=OrderSide.BUY,
                quantity=100,
                price=None,
                amount=1_000_000,
                trigger=OrderTrigger.SIGNAL,
                reason="위기 리플레이 테스트",
            )
            order_result = await self.risk.process_order(test_order)
            buy_blocked = not isinstance(order_result, ApprovedOrder)
            result["buy_blocked_correctly"] = buy_blocked == expected["buy_blocked"]

            # 5. 종합 판정
            result["all_passed"] = all([
                result["regime_match"],
                result["alert_match"],
                result["buy_blocked_correctly"],
            ])

            # 6. DB 저장
            await self._save_result(result)

        except Exception as e:
            logger.error("위기 리플레이 실패 [%s]: %s", crisis_name, e)
            result["error"] = str(e)
        finally:
            # 7. 복원
            await self.injector.restore()

        level = "✅" if result["all_passed"] else "❌"
        logger.info(
            "%s 위기 리플레이 [%s]: regime=%s alert=%s buy_block=%s",
            level, scenario["name"],
            result["regime_match"], result["alert_match"],
            result["buy_blocked_correctly"],
        )

        return result

    async def replay_all(self) -> list:
        """모든 위기 시나리오 리플레이"""
        results = []
        for name in HISTORICAL_CRISES:
            r = await self.replay(name)
            results.append(r)
        return results

    async def _save_result(self, result: dict) -> None:
        """crisis_replay_results 테이블에 저장"""
        try:
            async with get_session() as session:
                await session.execute(
                    text("""
                        INSERT INTO crisis_replay_results
                            (date, crisis_name, all_passed, regime_match,
                             alert_match, buy_blocked_correctly, details)
                        VALUES
                            (:dt, :name, :all, :regime, :alert, :buy, :details)
                    """),
                    {
                        "dt": date.today(),
                        "name": result.get("crisis", ""),
                        "all": result.get("all_passed", False),
                        "regime": result.get("regime_match", False),
                        "alert": result.get("alert_match", False),
                        "buy": result.get("buy_blocked_correctly", False),
                        "details": json.dumps(result, default=str, ensure_ascii=False),
                    },
                )
        except Exception as e:
            logger.error("crisis_replay_results 저장 실패: %s", e)
