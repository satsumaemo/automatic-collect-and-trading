"""
LLM Manager — 모델 관리 + 호출 로깅 + 비용 추적.

분석 유형별로 최적 모델을 자동 선택하고,
모든 호출을 llm_call_log 테이블에 기록합니다.
"""

import hashlib
import json
import logging
import time
from typing import Any, Optional

from sqlalchemy import text

from app.llm.gemini_client import GeminiClient, GEMINI_MODELS
from app.utils.db import get_session

logger = logging.getLogger(__name__)

# ── 분석 유형별 모델 매핑 ──
MODEL_MAP = {
    "regime": "pro",            # 핵심 판단 → 고급 모델
    "financial": "pro",         # 재무 분석
    "risk_detection": "flash",  # 속도+품질
    "news_overview": "flash",   # 중급
    "sentiment": "flash-lite",  # 대량 배치, 경량
    "validation": "flash",      # 속도 중요 (1~3초 이내)
    "post_review": "flash",     # 비동기
    "emergency": "flash",       # 긴급 분석
}

# ── 비용 추정 (1M 토큰당 USD) ──
COST_PER_1M = {
    "pro": {"input": 1.25, "output": 10.0},
    "flash": {"input": 0.15, "output": 0.60},
    "flash-lite": {"input": 0.075, "output": 0.30},
}


class LLMManager:
    """LLM 호출 추상화 — 모델 선택, JSON 파싱, DB 기록, 비용 추적"""

    def __init__(self) -> None:
        self.gemini = GeminiClient()
        logger.info("LLMManager 초기화")

    async def call(
        self,
        task_type: str,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Optional[dict]:
        """
        LLM 호출 메인 — 모델 자동선택 + JSON 파싱 + DB 기록.

        Args:
            task_type: 작업 유형 (regime, validation 등)
            prompt: 프롬프트 텍스트
            temperature: 생성 온도
            max_tokens: 최대 출력 토큰 수

        Returns:
            파싱된 JSON dict 또는 None
        """
        model_key = MODEL_MAP.get(task_type, "flash")
        model_name = GEMINI_MODELS[model_key]

        # 토큰 수 추정
        input_tokens = self.gemini.estimate_tokens(prompt)

        # LLM 호출
        start_time = time.time()
        result: Optional[dict] = None
        raw_output: str = ""

        try:
            result = await self.gemini.call_with_json(
                prompt, model_name, temperature, max_tokens
            )
        except Exception as e:
            logger.error("LLM 호출 실패 [%s]: %s", task_type, e)
            raw_output = str(e)

        elapsed = time.time() - start_time

        # 비용 계산
        if result is not None:
            raw_output = json.dumps(result, ensure_ascii=False)
            output_tokens = self.gemini.estimate_tokens(raw_output)
        else:
            output_tokens = 0

        cost = (
            input_tokens * COST_PER_1M[model_key]["input"]
            + output_tokens * COST_PER_1M[model_key]["output"]
        ) / 1_000_000

        # DB 기록
        await self._log_call(
            task_type=task_type,
            model=model_name,
            prompt=prompt,
            result=result,
            raw_output=raw_output,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        )

        logger.info(
            "LLM 호출 완료 [%s] model=%s 소요=%.1fs 토큰=%d→%d 비용=$%.4f 성공=%s",
            task_type, model_key, elapsed,
            input_tokens, output_tokens, cost,
            result is not None,
        )

        return result

    async def call_raw(
        self,
        task_type: str,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """
        LLM 호출 — raw text 반환 (JSON 파싱 없이).
        감성분석 등 간단한 응답에 사용.
        """
        model_key = MODEL_MAP.get(task_type, "flash")
        model_name = GEMINI_MODELS[model_key]

        try:
            return await self.gemini.call(prompt, model_name, temperature, max_tokens)
        except Exception as e:
            logger.error("LLM raw 호출 실패 [%s]: %s", task_type, e)
            return ""

    async def _log_call(
        self,
        task_type: str,
        model: str,
        prompt: str,
        result: Optional[dict],
        raw_output: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None:
        """llm_call_log 테이블에 호출 기록"""
        try:
            prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
            async with get_session() as session:
                session.execute(
                    text("""
                        INSERT INTO llm_call_log
                            (task_type, model_used, prompt_hash,
                             input_tokens, output_tokens, cost_usd,
                             input_summary, raw_output, parsed_output,
                             validation_passed)
                        VALUES
                            (:task, :model, :hash,
                             :in_tok, :out_tok, :cost,
                             :summary, :raw, :parsed,
                             :valid)
                    """),
                    {
                        "task": task_type,
                        "model": model,
                        "hash": prompt_hash,
                        "in_tok": input_tokens,
                        "out_tok": output_tokens,
                        "cost": cost,
                        "summary": prompt[:500],
                        "raw": raw_output[:10000] if raw_output else None,
                        "parsed": (
                            json.dumps(result, ensure_ascii=False)
                            if result
                            else None
                        ),
                        "valid": result is not None,
                    },
                )
        except Exception as e:
            # 로깅 실패가 메인 흐름을 중단시키면 안 됨
            logger.error("LLM 호출 로그 저장 실패: %s", e)
