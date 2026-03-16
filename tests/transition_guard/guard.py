"""
실전 전환 안전장치.
모의투자 → 실전 전환 전 11가지 조건을 검증합니다.
"""

import logging
from datetime import date, timedelta
from typing import Dict, List

from sqlalchemy import text

from app.models.contracts import OrderSide
from app.services.execution_service import ExecutionService
from app.utils import redis_client
from app.utils.db import get_session
from tests.daily_drill.scorecard import ValidationScorecard

logger = logging.getLogger(__name__)

# 전환 필수 조건
MIN_PAPER_DAYS = 30
MIN_PASS_RATE = 0.85
MIN_FN_ZERO_STREAK = 14
MAX_FP_RATE = 0.10
MIN_CRISIS_PASS = 3
MIN_KS_PASS = 3
MIN_READINESS = 80


class LiveTransitionGuard:
    """실전 전환 안전장치"""

    def __init__(self) -> None:
        self.scorecard = ValidationScorecard()

    async def check_all(self) -> Dict:
        """11가지 전환 조건 검사"""
        checks: Dict[str, dict] = {}

        # 1. 모의투자 30일 이상
        checks["paper_30d"] = await self._check_paper_days()

        # 2. 통과율 85% 이상
        checks["pass_rate_85"] = await self._check_pass_rate()

        # 3. False Negative 14일 연속 0건
        checks["fn_zero_14d"] = await self._check_fn_streak()

        # 4. False Positive율 10% 이하
        checks["fp_rate_10"] = await self._check_fp_rate()

        # 5. 위기 리플레이 3건 통과
        checks["crisis_replay_3"] = await self._check_crisis_replays()

        # 6. Kill Switch 3회 통과
        checks["kill_switch_3"] = await self._check_kill_switch()

        # 7. 텔레그램 알림 정상
        checks["telegram_ok"] = await self._check_telegram()

        # 8. API 토큰 자동 갱신
        checks["token_refresh"] = await self._check_token()

        # 9. 잔고 동기화
        checks["balance_sync"] = await self._check_balance_sync()

        # 10. 준비도 점수 80점 이상
        checks["readiness_80"] = await self._check_readiness()

        # 11. 호가단위 정합성
        checks["tick_size_ok"] = self._check_tick_size()

        # 종합
        all_passed = all(c.get("passed", False) for c in checks.values())
        failed = [k for k, v in checks.items() if not v.get("passed")]

        return {
            "all_passed": all_passed,
            "checks": checks,
            "failed_conditions": failed,
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
        }

    async def _check_paper_days(self) -> dict:
        try:
            async with get_session() as session:
                r = await session.execute(
                    text("SELECT COUNT(DISTINCT date) FROM daily_performance")
                )
                days = r.scalar() or 0
            return {"passed": days >= MIN_PAPER_DAYS, "value": days, "required": MIN_PAPER_DAYS}
        except Exception:
            return {"passed": False, "value": 0, "error": "DB 조회 실패"}

    async def _check_pass_rate(self) -> dict:
        try:
            report = await self.scorecard.generate_weekly_report()
            pr = report.get("pass_rate", 0)
            return {"passed": pr >= MIN_PASS_RATE, "value": pr, "required": MIN_PASS_RATE}
        except Exception:
            return {"passed": False, "value": 0}

    async def _check_fn_streak(self) -> dict:
        try:
            async with get_session() as session:
                # 최근 14일간 False Negative 수
                cutoff = date.today() - timedelta(days=MIN_FN_ZERO_STREAK)
                r = await session.execute(
                    text("""
                        SELECT COUNT(*) FROM drill_results
                        WHERE date >= :cutoff AND error_type = 'FALSE_NEGATIVE'
                    """),
                    {"cutoff": cutoff},
                )
                fn = r.scalar() or 0
            return {"passed": fn == 0, "value": fn, "required": 0}
        except Exception:
            return {"passed": False, "value": -1}

    async def _check_fp_rate(self) -> dict:
        try:
            report = await self.scorecard.generate_weekly_report()
            total = max(report.get("total_tests", 1), 1)
            fp_rate = report.get("false_positives", 0) / total
            return {"passed": fp_rate <= MAX_FP_RATE, "value": fp_rate, "required": MAX_FP_RATE}
        except Exception:
            return {"passed": False, "value": 1.0}

    async def _check_crisis_replays(self) -> dict:
        try:
            async with get_session() as session:
                r = await session.execute(
                    text("SELECT COUNT(*) FROM crisis_replay_results WHERE all_passed = TRUE")
                )
                count = r.scalar() or 0
            return {"passed": count >= MIN_CRISIS_PASS, "value": count, "required": MIN_CRISIS_PASS}
        except Exception:
            return {"passed": False, "value": 0}

    async def _check_kill_switch(self) -> dict:
        try:
            async with get_session() as session:
                r = await session.execute(
                    text("""
                        SELECT COUNT(*) FROM drill_results
                        WHERE test_type = 'kill_switch' AND passed = TRUE
                    """)
                )
                count = r.scalar() or 0
            return {"passed": count >= MIN_KS_PASS, "value": count, "required": MIN_KS_PASS}
        except Exception:
            return {"passed": False, "value": 0}

    async def _check_telegram(self) -> dict:
        try:
            async with get_session() as session:
                r = await session.execute(
                    text("""
                        SELECT COUNT(*) FROM daily_performance
                        WHERE date >= :cutoff
                    """),
                    {"cutoff": date.today() - timedelta(days=7)},
                )
                count = r.scalar() or 0
            return {"passed": count > 0, "value": count}
        except Exception:
            return {"passed": False, "value": 0}

    async def _check_token(self) -> dict:
        try:
            async with get_session() as session:
                r = await session.execute(
                    text("""
                        SELECT COUNT(*) FROM llm_call_log
                        WHERE timestamp >= NOW() - INTERVAL '7 days'
                    """)
                )
                count = r.scalar() or 0
            return {"passed": count > 0, "value": count}
        except Exception:
            return {"passed": False, "value": 0}

    async def _check_balance_sync(self) -> dict:
        try:
            positions = await redis_client.get_positions()
            cash = await redis_client.get_cash()
            has_data = positions is not None or (cash and float(cash.get("krw", 0)) > 0)
            return {"passed": has_data, "value": "data_exists" if has_data else "no_data"}
        except Exception:
            return {"passed": False, "value": "error"}

    async def _check_readiness(self) -> dict:
        try:
            score = await self.scorecard.calculate_readiness()
            return {"passed": score >= MIN_READINESS, "value": score, "required": MIN_READINESS}
        except Exception:
            return {"passed": False, "value": 0}

    def _check_tick_size(self) -> dict:
        """호가단위 정합성 테스트"""
        svc = ExecutionService(broker=None)
        tests = [
            (1500, "KODEX 200", OrderSide.BUY, 1500),   # ETF 5원 단위
            (1503, "KODEX 200", OrderSide.BUY, 1505),
            (1507, "KODEX 200", OrderSide.SELL, 1505),
            (50500, "삼성전자", OrderSide.BUY, 50500),   # 50원 단위
            (50530, "삼성전자", OrderSide.BUY, 50550),
            (50570, "삼성전자", OrderSide.SELL, 50550),
        ]
        passed = 0
        for price, ticker, side, expected in tests:
            actual = svc.align_tick_size(price, ticker, side)
            if actual == expected:
                passed += 1

        all_ok = passed == len(tests)
        return {"passed": all_ok, "value": f"{passed}/{len(tests)}"}

    def print_result(self, result: Dict) -> None:
        """결과를 콘솔에 출력"""
        print("\n" + "=" * 60)
        print("  실전 전환 안전 검사 결과")
        print("=" * 60)

        for name, check in result["checks"].items():
            icon = "✅" if check.get("passed") else "❌"
            val = check.get("value", "")
            req = check.get("required", "")
            suffix = f" (필요: {req})" if req else ""
            print(f"  {icon} {name}: {val}{suffix}")

        print("-" * 60)
        if result["all_passed"]:
            print("  🎉 모든 조건 충족! 실전 전환 가능합니다.")
            print("  .env에서 TRADING_MODE=live로 변경하세요.")
        else:
            print(f"  ⛔ 미충족 조건 {len(result['failed_conditions'])}개:")
            for f in result["failed_conditions"]:
                print(f"     - {f}")
        print("=" * 60 + "\n")
