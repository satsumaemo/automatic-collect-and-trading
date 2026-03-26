"""
Gemini API 클라이언트.
google-generativeai 라이브러리를 사용합니다.

JSON 추출, 토큰 추정, 자동 재시도를 지원합니다.
"""

import ast
import json
import logging
import re
from typing import Any, Optional

import google.generativeai as genai

from app.config import settings

logger = logging.getLogger(__name__)

# ── 사용 가능 모델명 ──
GEMINI_MODELS = {
    "pro": "gemini-2.5-pro",
    "flash": "gemini-2.5-flash",
    "flash-lite": "gemini-2.0-flash-lite",
}

# JSON 재시도 시 추가하는 강조 문구
JSON_ENFORCE_SUFFIX = "\n\n[중요] 반드시 유효한 JSON만 응답하세요. 다른 텍스트, 마크다운, 설명 없이 JSON 객체만 출력하세요."


def _extract_json_candidate(text: str) -> str:
    """LLM 응답에서 JSON 문자열 후보를 추출."""
    # 1) ```json ... ``` 블록
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    # 2) ``` ... ``` 블록 (json 태그 없이)
    match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        if candidate.startswith("{"):
            return candidate
    # 3) { ... } 전체 (가장 바깥 중괄호)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0)
    raise json.JSONDecodeError("JSON을 찾을 수 없음", text, 0)


def _fix_unescaped_quotes(json_str: str) -> str:
    """문자열 값 내부의 이스케이프 안 된 큰따옴표·줄바꿈을 제거."""
    # 문자열 값 내부("..." 안)에서 이스케이프 안 된 제어문자 제거
    cleaned = json_str.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    # "key": "value with "bad" quotes" → 패턴 기반 복구
    # description 등 긴 값 필드에서 내부 큰따옴표를 작은따옴표로 변환
    def _replace_inner_quotes(m: re.Match) -> str:
        key = m.group(1)
        value = m.group(2)
        fixed_value = value.replace('"', "'")
        return f'"{key}": "{fixed_value}"'
    # "key": "...내용..." 패턴에서 내용 부분에 큰따옴표가 있으면 교체
    # 키 뒤 값이 문자열인 경우를 찾되, 값 끝은 ", 또는 "} 또는 "] 로 판단
    cleaned = re.sub(
        r'"(description|reasoning|summary|detail|message|text)"'
        r'\s*:\s*"((?:[^"\\]|\\.)*(?:"(?![,\s*}\]])(?:[^"\\]|\\.)*)*)"',
        _replace_inner_quotes,
        cleaned,
    )
    return cleaned


def _extract_minimal_risk(text: str) -> Optional[dict]:
    """정규식으로 risk_detection 핵심 필드만 추출하여 최소 dict 반환."""
    alert_level = None
    alert_confidence = None

    m = re.search(r'"alert_level"\s*:\s*"(\w+)"', text)
    if m:
        alert_level = m.group(1)
    m = re.search(r'"alert_confidence"\s*:\s*([\d.]+)', text)
    if m:
        alert_confidence = float(m.group(1))

    if alert_level is not None:
        result = {"alert_level": alert_level}
        if alert_confidence is not None:
            result["alert_confidence"] = alert_confidence
        # detected_signals 개수 추출 시도
        signals = re.findall(r'"signal_type"\s*:\s*"([^"]*)"', text)
        if signals:
            result["detected_signals"] = [{"signal_type": s} for s in signals]
        else:
            result["detected_signals"] = []
        logger.warning("정규식 최소 추출 사용: %s", result)
        return result
    return None


def extract_json(text: str) -> dict:
    """LLM 응답에서 JSON 추출. 파싱 실패 시 단계적 복구 시도."""
    candidate = _extract_json_candidate(text)

    # 단계 1: 기본 json.loads
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # 단계 2: 이스케이프 안 된 큰따옴표/줄바꿈 정리 후 재시도
    try:
        fixed = _fix_unescaped_quotes(candidate)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 단계 3: ast.literal_eval (Python dict 리터럴로 파싱)
    try:
        result = ast.literal_eval(candidate)
        if isinstance(result, dict):
            return result
    except (ValueError, SyntaxError):
        pass

    # 단계 4: 정규식으로 핵심 필드만 추출 (risk_detection 전용)
    minimal = _extract_minimal_risk(text)
    if minimal:
        return minimal

    raise json.JSONDecodeError("모든 JSON 복구 시도 실패", text, 0)


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
