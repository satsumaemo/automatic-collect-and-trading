"""
결과 판정기.
LLM 검증 결과를 시나리오의 기대값과 비교하여 판정합니다.
"""

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 기대값별 허용 실제 결과
ACCEPT_MAP = {
    # 거부 기대 → reject 또는 defer면 통과
    "reject": {"reject", "defer"},
    # 보류 기대 → defer, reject, conditional_approve 모두 통과
    "defer": {"defer", "reject", "conditional_approve"},
    # 조건부 승인 기대 → conditional_approve, reject, defer 모두 통과
    "conditional_approve": {"conditional_approve", "reject", "defer"},
    # 승인 기대 → approve만 통과
    "approve": {"approve"},
}


class FaultTestJudge:
    """Fault Injection 결과 판정기"""

    def judge(self, llm_result: Optional[dict], scenario: dict) -> Dict:
        """
        LLM 검증 결과를 시나리오 기대값과 비교.

        Returns:
            {
                "passed": bool,
                "error_type": str or None,
                "expected": str,
                "actual": str,
                "severity": str,
                "scenario_name": str,
            }
        """
        expected = scenario.get("expected", "reject")
        severity = scenario.get("severity", "medium")
        name = scenario.get("name", "unknown")

        # LLM 결과가 없으면 — 안전 모드(conditional_approve)로 간주
        if llm_result is None:
            actual = "conditional_approve"
        elif isinstance(llm_result, dict):
            actual = llm_result.get("decision", "unknown")
        else:
            # ValidationResult 객체
            actual = getattr(llm_result, "decision", "unknown")

        acceptable = ACCEPT_MAP.get(expected, {expected})
        passed = actual in acceptable

        # 에러 유형 판별
        error_type = None
        if not passed:
            if expected in ("reject", "defer") and actual in ("approve", "conditional_approve"):
                # 위험한 거래를 통과시킴 → FALSE NEGATIVE (가장 위험)
                error_type = "FALSE_NEGATIVE"
            elif expected == "approve" and actual in ("reject", "defer"):
                # 정상 거래를 차단함 → FALSE POSITIVE (과잉 차단)
                error_type = "FALSE_POSITIVE"
            else:
                error_type = "MISMATCH"

        result = {
            "passed": passed,
            "error_type": error_type,
            "expected": expected,
            "actual": actual,
            "severity": severity,
            "scenario_name": name,
        }

        if passed:
            logger.info("✅ [%s] 통과: expected=%s actual=%s", name, expected, actual)
        else:
            logger.warning(
                "❌ [%s] 실패 (%s): expected=%s actual=%s severity=%s",
                name, error_type, expected, actual, severity,
            )

        return result
