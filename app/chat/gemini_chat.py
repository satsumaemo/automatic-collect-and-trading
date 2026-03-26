"""Gemini 멀티턴 대화 관리"""

import os
import google.generativeai as genai

from app.config import settings

CHAT_MODEL = os.getenv("CHAT_MODEL", "gemini-2.5-flash")


class GeminiChat:
    def __init__(self):
        genai.configure(api_key=settings.llm.gemini_api_key)
        self.model = genai.GenerativeModel(CHAT_MODEL)
        self.chat_session = None
        self.system_prompt = ""

    def start_session(self, system_prompt: str):
        """시스템 프롬프트로 새 세션 시작"""
        self.system_prompt = system_prompt
        self.chat_session = self.model.start_chat(history=[])

    def send_message(self, user_message: str, extra_context: str = "") -> str:
        """메시지 전송 + 응답 (동기)"""
        if self.chat_session is None:
            raise RuntimeError("세션이 시작되지 않았습니다")

        full_message = self._build_message(user_message, extra_context)
        response = self.chat_session.send_message(full_message)
        return response.text

    def send_message_stream(self, user_message: str, extra_context: str = ""):
        """스트리밍 응답 — yield로 청크 반환"""
        if self.chat_session is None:
            raise RuntimeError("세션이 시작되지 않았습니다")

        full_message = self._build_message(user_message, extra_context)
        response = self.chat_session.send_message(full_message, stream=True)
        for chunk in response:
            if chunk.text:
                yield chunk.text

    def reset(self):
        """세션 초기화"""
        self.chat_session = self.model.start_chat(history=[])

    def get_history_length(self) -> int:
        if self.chat_session:
            return len(self.chat_session.history)
        return 0

    def _build_message(self, user_message: str, extra_context: str) -> str:
        # 첫 메시지에는 시스템 프롬프트 포함
        if len(self.chat_session.history) == 0:
            full = f"{self.system_prompt}\n\n---\n\n사용자 질문: {user_message}"
        else:
            full = user_message

        if extra_context:
            full += f"\n\n[추가 참고 데이터]\n{extra_context}"
        return full
