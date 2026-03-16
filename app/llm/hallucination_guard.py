"""
할루시네이션 방어 모듈.
LLM 출력의 기본 일관성을 검증하고, 실패 시 이전 유효 판단을 반환합니다.
"""

import json
import logging
from typing import Optional

from sqlalchemy import text

from app.models.contracts import MacroSnapshot
from app.utils.db import get_session

logger = logging.getLogger(__name__)

# 유효한 열거형 값
VALID_REGIMES = {"expansion", "slowdown", "warning", "crisis"}
VALID_ALERT_LEVELS = {"normal", "caution", "warning", "emergency"}
VALID_DECISIONS = {"approve", "conditional_approve", "reject", "defer"}
VALID_ACTIONS = {"reduce_equity", "increase_cash", "add_hedge", "no_action"}


class HallucinationGuard:
    """LLM 출력 검증 및 폴백"""

    async def validate_regime_output(
        self,
        llm_output: dict,
        macro: MacroSnapshot,
    ) -> Optional[dict]:
        """
        레짐 분석 결과 검증.
        검증 실패 시 None 반환 (→ 호출자가 이전 판단 사용).
        """
        errors = []

        regime = llm_output.get("regime")
        confidence = llm_output.get("regime_confidence")
        allocation = llm_output.get("asset_allocation_suggestion", {})

        # 1. regime 유효값
        if regime not in VALID_REGIMES:
            errors.append(f"유효하지 않은 regime: {regime}")

        # 2. confidence 범위
        if confidence is not None and not (0.0 <= confidence <= 1.0):
            errors.append(f"confidence 범위 초과: {confidence}")

        # 3. VIX > 35인데 expansion?
        if macro.vix > 35 and regime == "expansion":
            errors.append(f"VIX={macro.vix}인데 expansion 판단 — 불일치")

        # 4. 금리 역전인데 reasoning에 언급 없음?
        if macro.yield_spread < 0:
            reasoning = llm_output.get("regime_reasoning", "")
            inversion_keywords = ["역전", "inversion", "inverted", "yield", "스프레드"]
            if not any(kw in reasoning.lower() for kw in inversion_keywords):
                errors.append("금리 역전 발생 중이나 reasoning에 관련 언급 없음")

        # 5. 자산 배분 합계 확인
        if allocation:
            numeric_keys = [k for k in allocation if k != "bond_duration"]
            alloc_sum = sum(
                float(allocation.get(k, 0)) for k in numeric_keys
            )
            if not (95 <= alloc_sum <= 105):
                errors.append(f"자산 배분 합계 비정상: {alloc_sum}%")

            # 주식 비율 0~80% 범위
            equity_sum = float(allocation.get("kr_equity", 0)) + float(
                allocation.get("us_equity", 0)
            )
            if equity_sum > 80:
                errors.append(f"주식 비율 80% 초과: {equity_sum}%")

        if errors:
            for err in errors:
                logger.warning("[할루시네이션] 레짐 검증 실패: %s", err)
            return None

        return llm_output

    async def validate_risk_output(self, llm_output: dict) -> Optional[dict]:
        """위험 감지 결과 검증."""
        errors = []

        alert_level = llm_output.get("alert_level")
        confidence = llm_output.get("alert_confidence")

        if alert_level not in VALID_ALERT_LEVELS:
            errors.append(f"유효하지 않은 alert_level: {alert_level}")

        if confidence is not None and not (0.0 <= confidence <= 1.0):
            errors.append(f"confidence 범위 초과: {confidence}")

        # recommended_actions 유효성
        for action in llm_output.get("recommended_actions", []):
            act = action.get("action")
            if act and act not in VALID_ACTIONS:
                errors.append(f"유효하지 않은 action: {act}")

        if errors:
            for err in errors:
                logger.warning("[할루시네이션] 리스크 검증 실패: %s", err)
            return None

        return llm_output

    async def validate_validation_output(self, llm_output: dict) -> Optional[dict]:
        """거래 사전검증 결과 검증."""
        errors = []

        decision = llm_output.get("decision")
        confidence = llm_output.get("confidence")

        # decision 유효값
        if decision not in VALID_DECISIONS:
            errors.append(f"유효하지 않은 decision: {decision}")

        # confidence 범위
        if confidence is not None and not (0.0 <= confidence <= 1.0):
            errors.append(f"confidence 범위 초과: {confidence}")

        # checks 5개 존재 확인
        checks = llm_output.get("checks", {})
        required_checks = {
            "logical_consistency",
            "event_timing",
            "news_conflict",
            "anomaly_detection",
            "portfolio_coherence",
        }
        missing = required_checks - set(checks.keys())
        if missing:
            errors.append(f"checks 누락: {missing}")

        # reduce_size_to_pct 범위
        modification = llm_output.get("modification", {})
        reduce_pct = modification.get("reduce_size_to_pct")
        if reduce_pct is not None and not (1 <= reduce_pct <= 100):
            errors.append(f"reduce_size_to_pct 범위 초과: {reduce_pct}")

        if errors:
            for err in errors:
                logger.warning("[할루시네이션] 검증 결과 실패: %s", err)
            return None

        return llm_output

    async def get_previous_valid(self, task_type: str) -> Optional[dict]:
        """DB에서 해당 task_type의 마지막 성공 기록 조회"""
        try:
            async with get_session() as session:
                result = await session.execute(
                    text("""
                        SELECT parsed_output FROM llm_call_log
                        WHERE task_type = :task AND validation_passed = TRUE
                        ORDER BY timestamp DESC LIMIT 1
                    """),
                    {"task": task_type},
                )
                row = result.fetchone()
                if row and row[0]:
                    # JSONB 컬럼이므로 이미 dict일 수 있음
                    if isinstance(row[0], dict):
                        return row[0]
                    return json.loads(row[0])
                return None
        except Exception as e:
            logger.error("이전 유효 판단 조회 실패: %s", e)
            return None
