# -*- coding: utf-8 -*-
"""DBReader 단위 테스트"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from app.chat.db_reader import DBReader

db = DBReader()
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

# 테스트 1: 레짐 조회
print("=== 테스트 1: 레짐 ===")
try:
    regime = db.get_latest_regime()
    print(f"  레짐: {regime.get('regime', 'N/A')}")
    print(f"  확신도: {regime.get('confidence', 'N/A')}")
    assert regime is not None, "레짐이 None"
    ok()
except Exception as e:
    fail(str(e))

# 테스트 2: 글로벌 지표
print("\n=== 테스트 2: 글로벌 지표 ===")
try:
    indicators = db.get_market_indicators()
    print(f"  지표 수: {len(indicators)}개")
    for ind in indicators[:3]:
        print(f"    {ind.get('name', '?')}: {ind.get('value', '?')}")
    ok()
except Exception as e:
    fail(str(e))

# 테스트 3: 최근 뉴스
print("\n=== 테스트 3: 최근 뉴스 ===")
try:
    news = db.get_recent_news(days=2, limit=5)
    print(f"  뉴스 수: {len(news)}건")
    for n in news[:2]:
        print(f"    [{n.get('source', '?')}] {str(n.get('title', '?'))[:50]}")
    ok()
except Exception as e:
    fail(str(e))

# 테스트 4: 카테고리별 뉴스
print("\n=== 테스트 4: 카테고리별 뉴스 ===")
try:
    news = db.get_news_by_category('sector_tech', days=7, limit=5)
    print(f"  sector_tech 뉴스: {len(news)}건")
    ok()
except Exception as e:
    fail(str(e))

# 테스트 5: 뉴스 버즈
print("\n=== 테스트 5: 뉴스 버즈 ===")
try:
    buzz = db.get_news_buzz()
    print(f"  버즈 급등 카테고리: {len(buzz)}개")
    for b in buzz:
        print(f"    {b.get('category', '?')}: buzz={b.get('buzz_score', '?')}")
    ok()
except Exception as e:
    fail(str(e))

# 테스트 6: LLM 분석 결과
print("\n=== 테스트 6: LLM 분석 결과 ===")
try:
    analyses = db.get_latest_analyses(limit=3)
    print(f"  분석 결과: {len(analyses)}건")
    for a in analyses:
        print(f"    {a.get('task_type', '?')} ({a.get('timestamp', '?')})")
    ok()
except Exception as e:
    fail(str(e))

# 테스트 7: 포지션
print("\n=== 테스트 7: 포지션 ===")
try:
    result = db.get_current_positions()
    positions = result.get("positions", [])
    cash = result.get("cash", 0)
    source = result.get("source", "unknown")
    print(f"  포지션: {len(positions)}개, 예수금: {cash:,.0f}원 (소스: {source})")
    ok()
except Exception as e:
    fail(str(e))

# 테스트 8: 거래 이력
print("\n=== 테스트 8: 거래 이력 ===")
try:
    trades = db.get_trade_history(days=7, limit=10)
    print(f"  거래: {len(trades)}건 (0건도 정상)")
    ok()
except Exception as e:
    fail(str(e))

# 테스트 9: 일별 성과
print("\n=== 테스트 9: 일별 성과 ===")
try:
    perf = db.get_daily_performance(days=7)
    print(f"  성과 데이터: {len(perf)}건 (0건도 정상)")
    ok()
except Exception as e:
    fail(str(e))

# 테스트 10: ETF 종목
print("\n=== 테스트 10: ETF 종목 ===")
try:
    etfs = db.get_etf_universe()
    print(f"  ETF: {len(etfs)}개")
    for etf in etfs[:3]:
        print(f"    {etf.get('ticker', '?')}: {etf.get('name', '?')}")
    assert len(etfs) > 0, "ETF 종목이 없음"
    ok()
except Exception as e:
    fail(str(e))

# 결과 요약
print("\n" + "="*50)
print(f"DBReader 단위 테스트 완료: PASS={passed}, FAIL={failed}")
if failed > 0:
    print("FAIL이 있으면 해당 메서드를 수정하고 다시 실행하세요")
    sys.exit(1)
