# -*- coding: utf-8 -*-
"""ContextBuilder 테스트"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from app.chat.db_reader import DBReader
from app.chat.context_builder import ContextBuilder

db = DBReader()
builder = ContextBuilder(db)

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

# 테스트 1: 시스템 프롬프트 생성
print("=== 테스트 1: 시스템 프롬프트 ===")
try:
    prompt = builder.build_system_prompt()
    print(f"  프롬프트 길이: {len(prompt)}자")
    print(f"  미리보기:\n{prompt[:500]}...")
    assert len(prompt) > 100, "프롬프트가 너무 짧음"
    assert "레짐" in prompt or "regime" in prompt.lower(), "레짐 섹션이 없음"
    ok()
except Exception as e:
    fail(str(e))

# 테스트 2: 토픽 감지 — 반도체
print("\n=== 테스트 2: 토픽 감지 (반도체) ===")
try:
    context = builder.build_topic_context("반도체 섹터 어떻게 봐?")
    print(f"  추가 컨텍스트 길이: {len(context)}자")
    if context:
        print(f"  미리보기: {context[:200]}...")
    print("  (빈 문자열도 OK — 뉴스가 없을 수 있음)")
    ok()
except Exception as e:
    fail(str(e))

# 테스트 3: 토픽 감지 — 금리
print("\n=== 테스트 3: 토픽 감지 (금리) ===")
try:
    context = builder.build_topic_context("금리 인하되면 채권 어떻게 될까?")
    print(f"  추가 컨텍스트 길이: {len(context)}자")
    ok()
except Exception as e:
    fail(str(e))

# 테스트 4: 토픽 감지 — 매칭 없음
print("\n=== 테스트 4: 토픽 감지 (매칭 없음) ===")
try:
    context = builder.build_topic_context("오늘 날씨 어때?")
    assert context == "" or context is None or len(context) == 0, "매칭 없는데 컨텍스트가 있음"
    print(f"  추가 컨텍스트: (없음) — 정상")
    ok()
except Exception as e:
    fail(str(e))

# 테스트 5: 빈 DB에서도 에러 없이 동작
print("\n=== 테스트 5: 프롬프트 안정성 ===")
try:
    prompt = builder.build_system_prompt()
    assert isinstance(prompt, str), "프롬프트가 문자열이 아님"
    assert "None" not in prompt or "데이터 없음" in prompt, "None이 프롬프트에 그대로 노출됨"
    ok()
except Exception as e:
    fail(str(e))

print("\n" + "="*50)
print(f"ContextBuilder 테스트 완료: PASS={passed}, FAIL={failed}")
if failed > 0:
    sys.exit(1)
