# -*- coding: utf-8 -*-
"""대화 품질 테스트 — 5개 시나리오"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from app.chat.db_reader import DBReader
from app.chat.context_builder import ContextBuilder
from app.chat.gemini_chat import GeminiChat

db = DBReader()
builder = ContextBuilder(db)

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

def new_chat():
    chat = GeminiChat()
    chat.start_session(builder.build_system_prompt())
    return chat

# 시나리오 1: 시장 전체 점검
print("=== 시나리오 1: 시장 전체 점검 ===")
try:
    chat = new_chat()
    extra = builder.build_topic_context("현재 시장 상황을 종합적으로 분석해줘")
    resp = chat.send_message("현재 시장 상황을 종합적으로 분석해줘", extra_context=extra)
    print(f"  응답 ({len(resp)}자):\n{resp[:400]}...\n")

    checks = []
    # 레짐 언급
    if any(kw in resp for kw in ["레짐", "둔화", "slowdown", "확장", "위기"]):
        checks.append("레짐 언급")
    # 지표 언급
    if any(kw in resp for kw in ["VIX", "금리", "S&P", "유가"]):
        checks.append("글로벌 지표")
    # 뉴스 동향
    if any(kw in resp for kw in ["뉴스", "중동", "FOMC", "관세", "전쟁"]):
        checks.append("뉴스 동향")

    print(f"  확인된 요소: {checks}")
    if len(checks) >= 2:
        ok(f"시장 분석 품질 양호 ({len(checks)}/3)")
    else:
        fail(f"시장 분석 품질 부족 ({len(checks)}/3)")
except Exception as e:
    fail(str(e))

# 시나리오 2: 반도체 섹터 토론
print("\n=== 시나리오 2: 반도체 섹터 토론 ===")
try:
    chat = new_chat()
    extra = builder.build_topic_context("반도체 섹터 지금 들어가도 될까?")
    resp = chat.send_message("반도체 섹터 지금 들어가도 될까?", extra_context=extra)
    print(f"  응답 ({len(resp)}자):\n{resp[:400]}...\n")

    checks = []
    if any(kw in resp for kw in ["반도체", "AI", "HBM", "삼성", "하이닉스", "엔비디아"]):
        checks.append("반도체 관련 내용")
    # 양면 분석
    pos_kw = ["긍정", "기회", "성장", "수요", "호재", "장점", "모멘텀"]
    neg_kw = ["부정", "리스크", "위험", "하락", "약점", "주의", "우려", "변동성"]
    if any(kw in resp for kw in pos_kw):
        checks.append("긍정 측면")
    if any(kw in resp for kw in neg_kw):
        checks.append("부정 측면")

    print(f"  확인된 요소: {checks}")
    if len(checks) >= 2:
        ok(f"반도체 분석 품질 양호 ({len(checks)}/3)")
    else:
        fail(f"반도체 분석 품질 부족 ({len(checks)}/3)")
except Exception as e:
    fail(str(e))

# 시나리오 3: 포트폴리오 질의
print("\n=== 시나리오 3: 포트폴리오 질의 ===")
try:
    chat = new_chat()
    resp = chat.send_message("지금 보유 포트폴리오에 대한 의견을 줘")
    print(f"  응답 ({len(resp)}자):\n{resp[:400]}...\n")

    checks = []
    if any(kw in resp for kw in ["포트폴리오", "포지션", "보유", "자산", "배분"]):
        checks.append("포트폴리오 언급")
    if any(kw in resp for kw in ["비중", "배분", "분산", "리밸런싱", "조정"]):
        checks.append("배분 코멘트")

    print(f"  확인된 요소: {checks}")
    if len(checks) >= 1:
        ok(f"포트폴리오 분석 양호 ({len(checks)}/2)")
    else:
        fail(f"포트폴리오 분석 부족 ({len(checks)}/2)")
except Exception as e:
    fail(str(e))

# 시나리오 4: 시스템 판단 질의
print("\n=== 시나리오 4: 시스템 판단 질의 ===")
try:
    chat = new_chat()
    resp = chat.send_message("시스템이 왜 이 레짐을 판단한 거야?")
    print(f"  응답 ({len(resp)}자):\n{resp[:400]}...\n")

    checks = []
    if any(kw in resp for kw in ["레짐", "둔화", "slowdown"]):
        checks.append("레짐 설명")
    if any(kw in resp for kw in ["VIX", "금리", "유가", "지표", "데이터", "근거"]):
        checks.append("판단 근거")

    print(f"  확인된 요소: {checks}")
    if len(checks) >= 1:
        ok(f"레짐 설명 양호 ({len(checks)}/2)")
    else:
        fail(f"레짐 설명 부족 ({len(checks)}/2)")
except Exception as e:
    fail(str(e))

# 시나리오 5: 후속 질문 (맥락 유지)
print("\n=== 시나리오 5: 후속 질문 (맥락 유지) ===")
try:
    chat = new_chat()
    r1 = chat.send_message("금리가 내리면 어떤 자산이 유리해?")
    print(f"  Q1 응답 ({len(r1)}자): {r1[:150]}...")
    r2 = chat.send_message("그럼 채권 ETF 중에 뭐가 좋을까?")
    print(f"  Q2 응답 ({len(r2)}자): {r2[:150]}...")
    r3 = chat.send_message("지금 시스템에서 채권 비중은 얼마야?")
    print(f"  Q3 응답 ({len(r3)}자): {r3[:150]}...")

    # 맥락 유지 확인: 금리/채권이 계속 언급되는지
    bond_kw = ["채권", "금리", "국채", "bond", "듀레이션"]
    r2_context = any(kw in r2 for kw in bond_kw)
    r3_context = any(kw in r3 for kw in bond_kw)

    if r2_context and r3_context:
        ok("3턴 대화 맥락 유지 양호")
    elif r2_context or r3_context:
        ok("대화 맥락 부분 유지")
    else:
        fail("대화 맥락 유지 실패")
except Exception as e:
    fail(str(e))

print(f"\n{'='*50}")
print(f"대화 품질 테스트 완료: PASS={passed}, FAIL={failed}")
if failed > 0:
    sys.exit(1)
