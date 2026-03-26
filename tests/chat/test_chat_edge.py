# -*- coding: utf-8 -*-
"""엣지 케이스 테스트"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from app.chat.db_reader import DBReader
from app.chat.context_builder import ContextBuilder
from app.chat.gemini_chat import GeminiChat

passed = 0
failed = 0

def ok(msg):
    global passed
    passed += 1
    print(f"  [PASS] {msg}")

def fail(msg):
    global failed
    failed += 1
    print(f"  [FAIL] {msg}")

db = DBReader()
builder = ContextBuilder(db)

# 6-1. 프롬프트에 None 노출 없는지
print("=== 6-1: 프롬프트 안정성 (None 노출) ===")
try:
    prompt = builder.build_system_prompt()
    # "None"이 데이터 값으로 나올 수 있지만, 섹션 자체가 None이면 안 됨
    assert isinstance(prompt, str)
    assert len(prompt) > 50
    ok("프롬프트 정상 생성")
except Exception as e:
    fail(str(e))

# 6-2. 빈 문자열 토픽 감지
print("\n=== 6-2: 빈 문자열 토픽 감지 ===")
try:
    context = builder.build_topic_context("")
    assert context == "", f"빈 입력에 컨텍스트가 있음: {len(context)}자"
    ok("빈 문자열 처리 정상")
except Exception as e:
    fail(str(e))

# 6-3. 매우 긴 입력 토픽 감지
print("\n=== 6-3: 긴 입력 토픽 감지 ===")
try:
    long_msg = "반도체 " * 500  # 2000자+
    context = builder.build_topic_context(long_msg)
    ok(f"긴 입력 처리 정상 (컨텍스트: {len(context)}자)")
except Exception as e:
    fail(str(e))

# 6-4. 특수문자/이모지 토픽 감지
print("\n=== 6-4: 특수문자/이모지 입력 ===")
try:
    context = builder.build_topic_context("AI가 대세! 🚀🔥 반도체 가즈아~~~")
    ok(f"특수문자/이모지 처리 정상 (컨텍스트: {len(context)}자)")
except Exception as e:
    fail(str(e))

# 6-5. 영어 입력 토픽 감지
print("\n=== 6-5: 영어 입력 ===")
try:
    # "AI"는 sector_tech 키워드에 있음
    context = builder.build_topic_context("What about AI stocks?")
    ok(f"영어 입력 처리 정상 (컨텍스트: {len(context)}자)")
except Exception as e:
    fail(str(e))

# 6-6. GeminiChat — 세션 없이 메시지 전송
print("\n=== 6-6: 세션 없이 메시지 전송 ===")
try:
    chat = GeminiChat()
    # 세션 시작 안 하고 바로 전송
    try:
        chat.send_message("test")
        fail("RuntimeError가 발생해야 함")
    except RuntimeError as e:
        ok(f"세션 없음 에러 정상: {e}")
except Exception as e:
    fail(str(e))

# 6-7. GeminiChat — 세션 없이 스트리밍
print("\n=== 6-7: 세션 없이 스트리밍 ===")
try:
    chat = GeminiChat()
    try:
        for chunk in chat.send_message_stream("test"):
            pass
        fail("RuntimeError가 발생해야 함")
    except RuntimeError as e:
        ok(f"세션 없음 에러 정상: {e}")
except Exception as e:
    fail(str(e))

# 6-8. DBReader — 잘못된 카테고리
print("\n=== 6-8: 존재하지 않는 카테고리 뉴스 ===")
try:
    news = db.get_news_by_category("nonexistent_category_xyz", days=7, limit=5)
    assert isinstance(news, list)
    ok(f"존재하지 않는 카테고리 처리 정상 (결과: {len(news)}건)")
except Exception as e:
    fail(str(e))

# 6-9. GeminiChat — 10턴 대화
print("\n=== 6-9: 10턴 대화 테스트 ===")
try:
    chat = GeminiChat()
    system_prompt = builder.build_system_prompt()
    chat.start_session(system_prompt)

    questions = [
        "현재 시장 한줄 요약해줘",
        "그럼 채권은?",
        "금은 어때?",
        "달러 강세가 지속될까?",
        "유가 전망은?",
        "국내 주식은?",
        "반도체는?",
        "2차전지는?",
        "배당주는?",
        "결론은?",
    ]
    for i, q in enumerate(questions, 1):
        resp = chat.send_message(q)
        assert len(resp) > 5, f"턴 {i}: 응답이 너무 짧음"
        print(f"  턴 {i}: {len(resp)}자")

    history_len = chat.get_history_length()
    assert history_len >= 20, f"이력이 20턴 미만: {history_len}"
    ok(f"10턴 대화 완료 (이력: {history_len})")
except Exception as e:
    fail(str(e))

print(f"\n{'='*50}")
print(f"엣지 케이스 테스트 완료: PASS={passed}, FAIL={failed}")
if failed > 0:
    sys.exit(1)
