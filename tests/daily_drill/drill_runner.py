"""
매일 아침 자동 훈련 (08:30 장 시작 전).
3가지 테스트: Fault Injection, Kill Switch 생존, 이벤트 인지.
"""

import logging
import random
from datetime import date, datetime
from typing import Dict, List

from sqlalchemy import text

from app.models.contracts import (
    OrderSide,
    OrderTrigger,
    ProposedOrder,
    ValidationResult,
)
from app.utils.db import get_session
from app.utils.telegram import notifier
from tests.fault_injection.injector import FaultInjector
from tests.fault_injection.judge import FaultTestJudge
from tests.fault_injection.scenarios import FAULT_SCENARIOS

logger = logging.getLogger(__name__)

# 매일 테스트할 Fault Injection 시나리오 수
DAILY_FAULT_COUNT = 2


class DailyDrill:
    """매일 아침 자동 훈련"""

    def __init__(self, risk_service, analysis_service, data_service) -> None:
        self.risk = risk_service
        self.analysis = analysis_service
        self.data = data_service
        self.injector = FaultInjector()
        self.judge = FaultTestJudge()

    async def run(self) -> Dict:
        """3가지 테스트 실행 → 결과 반환"""
        logger.info("=== 아침 훈련 시작 ===")
        results = {
            "date": date.today().isoformat(),
            "fault_injection": [],
            "kill_switch_ok": False,
            "event_awareness_ok": False,
            "all_passed": False,
        }

        # 1. Fault Injection (랜덤 2개)
        try:
            fi_results = await self._run_fault_injection()
            results["fault_injection"] = fi_results
        except Exception as e:
            logger.error("Fault Injection 실패: %s", e)

        # 2. Kill Switch 생존 확인
        try:
            results["kill_switch_ok"] = self._test_kill_switch()
        except Exception as e:
            logger.error("Kill Switch 테스트 실패: %s", e)

        # 3. 이벤트 인지 확인
        try:
            results["event_awareness_ok"] = await self._test_event_awareness()
        except Exception as e:
            logger.error("이벤트 인지 테스트 실패: %s", e)

        # 종합 판정
        fi_all_pass = all(r.get("passed", False) for r in results["fault_injection"])
        results["all_passed"] = (
            fi_all_pass
            and results["kill_switch_ok"]
            and results["event_awareness_ok"]
        )

        # 결과 저장 + 알림
        await self._save_results(results)
        await self._report(results)

        level = "✅" if results["all_passed"] else "🚨"
        logger.info("%s 아침 훈련 완료: %s", level, results["all_passed"])
        return results

    async def _run_fault_injection(self) -> List[Dict]:
        """랜덤 시나리오 N개 선택 → 주입 → LLM 검증 → 판정"""
        scenario_keys = random.sample(
            list(FAULT_SCENARIOS.keys()),
            min(DAILY_FAULT_COUNT, len(FAULT_SCENARIOS)),
        )

        results = []
        for key in scenario_keys:
            scenario = FAULT_SCENARIOS[key]
            try:
                # 주입
                await self.injector.setup_scenario(scenario)

                # 주문 구성
                fo = scenario["fake_order"]
                order = ProposedOrder(
                    ticker=fo["ticker"],
                    side=OrderSide.BUY if fo["side"] == "buy" else OrderSide.SELL,
                    quantity=max(1, fo["amount"] // 10000),
                    price=None,
                    amount=fo["amount"],
                    trigger=OrderTrigger(fo.get("trigger", "signal")),
                    reason=f"Fault Injection: {scenario['name']}",
                )

                # LLM 검증
                system_state = self.injector.build_system_state()
                validation = await self.analysis.validate_trade(order, system_state)

                # 판정
                judgment = self.judge.judge(validation, scenario)
                judgment["scenario_id"] = key
                results.append(judgment)

            except Exception as e:
                logger.error("시나리오 %s 실행 실패: %s", key, e)
                results.append({
                    "scenario_id": key,
                    "scenario_name": scenario["name"],
                    "passed": False,
                    "error_type": "EXECUTION_ERROR",
                    "expected": scenario.get("expected", "?"),
                    "actual": "error",
                    "severity": scenario.get("severity", "medium"),
                })
            finally:
                await self.injector.restore()

        return results

    def _test_kill_switch(self) -> bool:
        """Kill Switch 정상 응답 확인"""
        test_order = ProposedOrder(
            ticker="KODEX 200",
            side=OrderSide.BUY,
            quantity=10,
            price=None,
            amount=500_000,
            trigger=OrderTrigger.REBALANCE,
            reason="Kill Switch 테스트",
        )
        try:
            ks = self.risk.check_kill_switch(test_order)
            # 응답 자체가 정상이면 OK (통과/차단 무관)
            return hasattr(ks, "passed")
        except Exception as e:
            logger.error("Kill Switch 테스트 예외: %s", e)
            return False

    async def _test_event_awareness(self) -> bool:
        """이벤트 인지: data_service.get_economic_events() 호출 가능 확인"""
        try:
            events = await self.data.get_economic_events(days=7)
            # 호출 자체가 성공하면 OK (이벤트 0건도 정상)
            return isinstance(events, list)
        except Exception as e:
            logger.error("이벤트 인지 테스트 실패: %s", e)
            return False

    async def _save_results(self, results: Dict) -> None:
        """drill_results 테이블에 저장"""
        try:
            async with get_session() as session:
                # Fault Injection 결과 저장
                for fi in results.get("fault_injection", []):
                    session.execute(
                        text("""
                            INSERT INTO drill_results
                                (date, test_type, scenario_name, passed, error_type,
                                 target_check, expected_decision, actual_decision,
                                 severity)
                            VALUES
                                (:dt, 'fault_injection', :name, :passed, :err,
                                 :check, :expected, :actual, :severity)
                        """),
                        {
                            "dt": date.today(),
                            "name": fi.get("scenario_name", ""),
                            "passed": fi.get("passed", False),
                            "err": fi.get("error_type"),
                            "check": fi.get("target_check", ""),
                            "expected": fi.get("expected", ""),
                            "actual": fi.get("actual", ""),
                            "severity": fi.get("severity", ""),
                        },
                    )

                # Kill Switch 결과
                session.execute(
                    text("""
                        INSERT INTO drill_results
                            (date, test_type, scenario_name, passed)
                        VALUES (:dt, 'kill_switch', 'kill_switch_survival', :passed)
                    """),
                    {"dt": date.today(), "passed": results.get("kill_switch_ok", False)},
                )

                # 이벤트 인지 결과
                session.execute(
                    text("""
                        INSERT INTO drill_results
                            (date, test_type, scenario_name, passed)
                        VALUES (:dt, 'event_awareness', 'event_check', :passed)
                    """),
                    {"dt": date.today(), "passed": results.get("event_awareness_ok", False)},
                )
        except Exception as e:
            logger.error("drill_results 저장 실패: %s", e)

    async def _report(self, results: Dict) -> None:
        """텔레그램 알림"""
        fi_results = results.get("fault_injection", [])
        fi_pass = sum(1 for r in fi_results if r.get("passed"))
        fi_total = len(fi_results)
        fi_fn = sum(1 for r in fi_results if r.get("error_type") == "FALSE_NEGATIVE")

        if results["all_passed"]:
            msg = (
                f"✅ <b>아침 훈련 통과</b>\n"
                f"Fault Injection: {fi_pass}/{fi_total}\n"
                f"Kill Switch: OK\n"
                f"이벤트 인지: OK"
            )
        else:
            failures = []
            if fi_pass < fi_total:
                failures.append(f"FI: {fi_pass}/{fi_total} (FN={fi_fn})")
            if not results.get("kill_switch_ok"):
                failures.append("Kill Switch 장애")
            if not results.get("event_awareness_ok"):
                failures.append("이벤트 인지 실패")

            msg = (
                f"🚨 <b>아침 훈련 실패!</b>\n"
                f"실패 항목: {', '.join(failures)}"
            )

        try:
            await notifier.send_message(msg, "system_error")
        except Exception as e:
            logger.error("훈련 결과 알림 실패: %s", e)
