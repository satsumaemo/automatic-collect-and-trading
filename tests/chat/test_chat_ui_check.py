# -*- coding: utf-8 -*-
"""Streamlit UI 구조 검증 — 코드 수준 체크리스트"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

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

# app.py 소스 읽기
with open("app/chat/app.py", encoding="utf-8") as f:
    app_src = f.read()

print("=== Streamlit UI 체크리스트 ===\n")

# 1. 페이지 로드 (set_page_config)
if "set_page_config" in app_src:
    ok("1. 페이지 설정 존재")
else:
    fail("1. set_page_config 없음")

# 2. 사이드바에 레짐 표시
if "시장 레짐" in app_src and "regime" in app_src:
    ok("2. 사이드바 레짐 표시")
else:
    fail("2. 사이드바 레짐 표시 없음")

# 3. 사이드바에 뉴스 표시
if "오늘의 뉴스" in app_src or "뉴스" in app_src:
    ok("3. 사이드바 뉴스 표시")
else:
    fail("3. 사이드바 뉴스 없음")

# 4. 빠른 질문 버튼 4개
quick_buttons = ["현재 시장 분석", "주목할 뉴스", "포트폴리오 점검", "반도체 섹터 분석"]
found = sum(1 for b in quick_buttons if b in app_src)
if found == 4:
    ok(f"4. 빠른 질문 버튼 {found}/4개")
else:
    fail(f"4. 빠른 질문 버튼 {found}/4개만 발견")

# 5. Gemini 응답 호출
if "send_message" in app_src:
    ok("5. Gemini 응답 호출 존재")
else:
    fail("5. send_message 호출 없음")

# 6. 스트리밍 응답
if "send_message_stream" in app_src:
    ok("6. 스트리밍 응답 구현")
else:
    fail("6. 스트리밍 미구현")

# 7. 대화 이력 관리
if "st.session_state.messages" in app_src and "chat_message" in app_src:
    ok("7. 대화 이력 + chat_message 표시")
else:
    fail("7. 대화 이력 관리 없음")

# 8. 토픽 컨텍스트 반영
if "build_topic_context" in app_src:
    ok("8. 토픽 컨텍스트 반영")
else:
    fail("8. build_topic_context 호출 없음")

# 9. 새 대화 시작 버튼
if "새 대화 시작" in app_src and "reset" in app_src:
    ok("9. 새 대화 시작 버튼 + reset")
else:
    fail("9. 새 대화 시작 없음")

# 10. 데이터 새로고침 버튼
if "데이터 새로고침" in app_src:
    ok("10. 데이터 새로고침 버튼")
else:
    fail("10. 데이터 새로고침 없음")

print(f"\n{'='*50}")
print(f"UI 체크리스트 완료: PASS={passed}, FAIL={failed}")
if failed > 0:
    sys.exit(1)
