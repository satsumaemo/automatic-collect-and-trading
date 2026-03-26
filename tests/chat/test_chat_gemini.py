# -*- coding: utf-8 -*-
"""GeminiChat 테스트 — 실제 Gemini API 호출"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from app.chat.db_reader import DBReader
from app.chat.context_builder import ContextBuilder
from app.chat.gemini_chat import GeminiChat

db = DBReader()
builder = ContextBuilder(db)
chat = GeminiChat()

passed = 0
failed = 0

def ok(msg=""):
    global passed
    passed += 1
    print(f"  [PASS] {msg}")

def fail(msg=""):
    global failed
    failed += 1
    print(f"  [FAIL] {msg}")

# 테스트 1: 세션 시작
print("=== 테스트 1: 세션 시작 ===")
try:
    system_prompt = builder.build_system_prompt()
    chat.start_session(system_prompt)
    print(f"  시스템 프롬프트: {len(system_prompt)}자")
    ok("세션 시작 성공")
except Exception as e:
    fail(str(e))

# 테스트 2: 첫 메시지 (동기)
print("\n=== 테스트 2: 첫 메시지 (동기) ===")
try:
    response = chat.send_message("현재 시장 상황을 간단히 요약해줘")
    print(f"  응답 길이: {len(response)}자")
    print(f"  응답 미리보기: {response[:200]}...")
    assert len(response) > 10, "응답이 너무 짧음"
    ok()
except Exception as e:
    fail(str(e))

# 테스트 3: 후속 메시지 (대화 이력 유지)
print("\n=== 테스트 3: 후속 메시지 ===")
try:
    response = chat.send_message("좀 더 자세히 설명해줄 수 있어?")
    print(f"  응답 길이: {len(response)}자")
    assert len(response) > 10, "응답이 너무 짧음"
    history_len = chat.get_history_length()
    print(f"  대화 이력: {history_len}턴")
    assert history_len >= 4, f"이력이 4턴 미만: {history_len}"
    ok()
except Exception as e:
    fail(str(e))

# 테스트 4: 토픽 컨텍스트 포함 메시지
print("\n=== 테스트 4: 토픽 컨텍스트 포함 ===")
try:
    extra = builder.build_topic_context("반도체 섹터 어떻게 봐?")
    response = chat.send_message("반도체 섹터 어떻게 봐?", extra_context=extra)
    print(f"  응답 길이: {len(response)}자")
    print(f"  응답 미리보기: {response[:200]}...")
    ok()
except Exception as e:
    fail(str(e))

# 테스트 5: 스트리밍 응답
print("\n=== 테스트 5: 스트리밍 응답 ===")
try:
    chunks = []
    for chunk in chat.send_message_stream("금리 전망은?"):
        chunks.append(chunk)
    full = "".join(chunks)
    print(f"  청크 수: {len(chunks)}개")
    print(f"  전체 응답: {len(full)}자")
    assert len(chunks) > 0, "스트리밍 청크가 없음"
    assert len(full) > 10, "응답이 너무 짧음"
    ok()
except Exception as e:
    fail(str(e))

# 테스트 6: 세션 리셋
print("\n=== 테스트 6: 세션 리셋 ===")
try:
    chat.reset()
    history_len = chat.get_history_length()
    assert history_len == 0, f"리셋 후 이력이 남아있음: {history_len}"
    ok()
except Exception as e:
    fail(str(e))

print("\n" + "="*50)
print(f"GeminiChat 테스트 완료: PASS={passed}, FAIL={failed}")
if failed > 0:
    sys.exit(1)
