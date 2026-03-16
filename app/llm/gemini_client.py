"""
Gemini API 클라이언트.
google-generativeai 라이브러리를 사용합니다.

JSON 추출, 토큰 추정, 자동 재시도를 지원합니다.
"""

import json
import logging
import re
from typing import Any, Optional

import google.generativeai as genai

from app.config import settings

logger = logging.getLogger(__name__)

# ── 사용 가능 모델명 ──
GEMINI_MODELS = {
    "pro": "gemini-2.5-pro-preview-06-05",
    "flash": "gemini-2.5-flash-preview-05-20",
    "flash-lite": "gemini-2.0-flash-lite",
}

# JSON 재시도 시 추가하는 강조 문구
JSON_ENFORCE_SUFFIX = "\n\n[중요] 반드시 유효한 JSON만 응답하세요. 다른 텍스트, 마크다운, 설명 없이 JSON 객체만 출력하세요."


def extract_json(text: str) -> dict:
    """LLM 응답에서 JSON 추출. 다양한 형태 대응."""
    # 1) ```json ... ``` 블록
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    # 2) ``` ... ``` 블록 (json 태그 없이)
    match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        if candidate.startswith("{"):
            return json.loads(candidate)
    # 3) { ... } 전체 (가장 바깥 중괄호)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise json.JSONDecodeError("JSON을 찾을 수 없음", text, 0)


class GeminiClient:
    """Google Gemini API 클라이언트"""

    def __init__(self) -> None:
        api_key = settings.llm.gemini_api_key
        if not api_key:
            logger.warning("GEMINI_API_KEY가 설정되지 않았습니다")
        genai.configure(api_key=api_key)
        logger.info("GeminiClient 초기화")

    async def call(
        self,
        prompt: str,
        model_name: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """
        Gemini API 호출 → 응답 텍스트 반환.

        Args:
            prompt: 프롬프트 텍스트
            model_name: 전체 모델 이름 (GEMINI_MODELS의 value)
            temperature: 생성 온도
            max_tokens: 최대 출력 토큰 수

        Returns:
            응답 텍스트 (raw)
        """
        model = genai.GenerativeModel(model_name)
        generation_config = genai.types.GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        response = await model.generate_content_async(
            prompt,
            generation_config=generation_config,
        )

        if not response or not response.text:
            logger.warning("Gemini 빈 응답: model=%s", model_name)
            return ""

        return response.text

    async def call_with_json(
        self,
        prompt: str,
        model_name: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        max_retries: int = 3,
    ) -> Optional[dict]:
        """
        Gemini API 호출 + JSON 파싱.
        파싱 실패 시 max_retries까지 재시도 (JSON 강조 문구 추가).
        최종 실패 시 None 반환.
        """
        current_prompt = prompt

        for attempt in range(1, max_retries + 1):
            try:
                raw_text = await self.call(
                    current_prompt, model_name, temperature, max_tokens
                )
                if not raw_text:
                    logger.warning("빈 응답 (시도 %d/%d)", attempt, max_retries)
                    continue

                parsed = extract_json(raw_text)
                return parsed

            except json.JSONDecodeError as e:
                logger.warning(
                    "JSON 파싱 실패 (시도 %d/%d): %s — 응답 앞 200자: %s",
                    attempt, max_retries, e,
                    raw_text[:200] if raw_text else "(없음)",
                )
                # 재시도 시 JSON 강조 문구 추가
                current_prompt = prompt + JSON_ENFORCE_SUFFIX

            except Exception as e:
                logger.error(
                    "Gemini API 호출 실패 (시도 %d/%d): %s",
                    attempt, max_retries, e,
                )
                if attempt == max_retries:
                    return None

        logger.error("Gemini JSON 호출 최종 실패 (%d회 시도)", max_retries)
        return None

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """
        대략적 토큰 수 추정.
        한국어: 글자수 × 1.5
        영어:   단어수 × 1.3
        혼합:   (한국어 글자 × 1.5 + 영어 단어 × 1.3)
        """
        if not text:
            return 0

        # 한국어 글자 수
        korean_chars = len(re.findall(r"[가-힣]", text))
        # 영어/숫자 단어 수
        non_korean = re.sub(r"[가-힣]", " ", text)
        english_words = len(non_korean.split())

        return int(korean_chars * 1.5 + english_words * 1.3)
