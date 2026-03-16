"""16개 이상거래 감지 시나리오 — LLM 사전검증 테스트용"""

FAULT_SCENARIOS = {
    # ═══ 검사 1: 논리적 일관성 ═══
    "LC_01": {
        "name": "둔화기에 공격적 매수",
        "target_check": "logical_consistency",
        "setup": {
            "force_regime": "slowdown",
            "morning_analysis": "경기 둔화 진입, 주식 비중 축소 권장",
        },
        "fake_order": {"side": "buy", "ticker": "KODEX 레버리지", "amount": 5_000_000, "trigger": "signal"},
        "expected": "reject",
        "severity": "critical",
    },
    "LC_02": {
        "name": "위기기에 신규 포지션",
        "target_check": "logical_consistency",
        "setup": {"force_regime": "crisis", "alert_level": "emergency"},
        "fake_order": {"side": "buy", "ticker": "KODEX 200", "amount": 2_000_000, "trigger": "signal"},
        "expected": "reject",
        "severity": "critical",
    },
    "LC_03": {
        "name": "과열 섹터 추가 매수",
        "target_check": "logical_consistency",
        "setup": {"morning_analysis": "AI 반도체 섹터 과열 주의"},
        "fake_order": {"side": "buy", "ticker": "KODEX 반도체", "amount": 3_000_000, "trigger": "signal"},
        "expected": "conditional_approve",
        "severity": "medium",
    },

    # ═══ 검사 2: 이벤트 타이밍 ═══
    "ET_01": {
        "name": "FOMC 당일 대규모 매수",
        "target_check": "event_timing",
        "setup": {"inject_event": {"name": "FOMC 금리 결정", "hours_away": 8, "importance": "critical"}},
        "fake_order": {"side": "buy", "ticker": "KODEX 200", "amount": 5_000_000, "trigger": "rebalance"},
        "expected": "defer",
        "severity": "high",
    },
    "ET_02": {
        "name": "CPI 발표일 채권 대량 매매",
        "target_check": "event_timing",
        "setup": {"inject_event": {"name": "US CPI 발표", "hours_away": 4, "importance": "high"}},
        "fake_order": {"side": "buy", "ticker": "KODEX 국고채10년", "amount": 3_000_000, "trigger": "rebalance"},
        "expected": "conditional_approve",
        "severity": "medium",
    },
    "ET_03": {
        "name": "실적발표 당일 해당 섹터 매수",
        "target_check": "event_timing",
        "setup": {"inject_event": {"name": "삼성전자 실적발표", "hours_away": 6, "importance": "high"}},
        "fake_order": {"side": "buy", "ticker": "KODEX 반도체", "amount": 2_000_000, "trigger": "signal"},
        "expected": "defer",
        "severity": "medium",
    },

    # ═══ 검사 3: 뉴스 충돌 ═══
    "NC_01": {
        "name": "부정 뉴스 직후 해당 섹터 매수",
        "target_check": "news_conflict",
        "setup": {"inject_news": {"title": "반도체 대규모 감산 발표, 수요 급감", "sentiment": -0.85, "minutes_ago": 30}},
        "fake_order": {"side": "buy", "ticker": "KODEX 반도체", "amount": 2_000_000, "trigger": "signal"},
        "expected": "reject",
        "severity": "critical",
    },
    "NC_02": {
        "name": "은행 위기 뉴스 중 금융 ETF 매수",
        "target_check": "news_conflict",
        "setup": {"inject_news": {"title": "미국 지역은행 연쇄 파산 우려", "sentiment": -0.75, "minutes_ago": 120}},
        "fake_order": {"side": "buy", "ticker": "KODEX 은행", "amount": 1_500_000, "trigger": "signal"},
        "expected": "reject",
        "severity": "high",
    },
    "NC_03": {
        "name": "긍정 뉴스와 일치 (통과해야 함)",
        "target_check": "news_conflict",
        "setup": {"inject_news": {"title": "AI 반도체 수요 폭발, 엔비디아 서프라이즈", "sentiment": 0.85, "minutes_ago": 60}},
        "fake_order": {"side": "buy", "ticker": "KODEX 반도체", "amount": 1_000_000, "trigger": "signal"},
        "expected": "approve",
        "severity": "low",
    },

    # ═══ 검사 4: 이상 패턴 ═══
    "AD_01": {
        "name": "거래량 10배 급증 중 매수",
        "target_check": "anomaly_detection",
        "setup": {"inject_market_data": {"ticker": "KODEX 200", "volume_ratio": 10.0}},
        "fake_order": {"side": "buy", "ticker": "KODEX 200", "amount": 2_000_000, "trigger": "signal"},
        "expected": "conditional_approve",
        "severity": "medium",
    },
    "AD_02": {
        "name": "ETF NAV 괴리율 3% 초과",
        "target_check": "anomaly_detection",
        "setup": {"inject_market_data": {"ticker": "KODEX 골드선물(H)", "nav_premium": 0.035}},
        "fake_order": {"side": "buy", "ticker": "KODEX 골드선물(H)", "amount": 1_000_000, "trigger": "rebalance"},
        "expected": "conditional_approve",
        "severity": "medium",
    },

    # ═══ 검사 5: 포트폴리오 정합성 ═══
    "PC_01": {
        "name": "단일 섹터 40% 초과",
        "target_check": "portfolio_coherence",
        "setup": {"set_portfolio": {"KODEX 반도체": {"pct": 30}, "TIGER AI반도체핵심공정": {"pct": 8}}},
        "fake_order": {"side": "buy", "ticker": "KODEX 반도체", "amount": 3_000_000, "trigger": "signal"},
        "expected": "conditional_approve",
        "severity": "medium",
    },
    "PC_02": {
        "name": "롱+숏 동시 보유",
        "target_check": "portfolio_coherence",
        "setup": {"set_portfolio": {"KODEX 200": {"pct": 20}, "KODEX 인버스": {"pct": 5}}},
        "fake_order": {"side": "buy", "ticker": "KODEX 인버스", "amount": 2_000_000, "trigger": "signal"},
        "expected": "reject",
        "severity": "high",
    },

    # ═══ 복합 위반 ═══
    "MULTI_01": {
        "name": "3중 위반: 둔화기+FOMC+부정뉴스",
        "target_check": "multiple",
        "setup": {
            "force_regime": "slowdown",
            "inject_event": {"name": "FOMC", "hours_away": 4, "importance": "critical"},
            "inject_news": {"title": "경기침체 우려 확산", "sentiment": -0.7, "minutes_ago": 60},
        },
        "fake_order": {"side": "buy", "ticker": "KODEX 레버리지", "amount": 5_000_000, "trigger": "signal"},
        "expected": "reject",
        "severity": "critical",
    },

    # ═══ 역테스트: 정상 통과 ═══
    "NORMAL_01": {
        "name": "정상 리밸런싱 (모든 조건 양호)",
        "target_check": "none",
        "setup": {"force_regime": "expansion", "clear_events": True, "clear_negative_news": True},
        "fake_order": {"side": "buy", "ticker": "KODEX 200", "amount": 500_000, "trigger": "rebalance"},
        "expected": "approve",
        "severity": "low",
    },
    "NORMAL_02": {
        "name": "소액 면제 (10만원 미만)",
        "target_check": "exempt",
        "setup": {},
        "fake_order": {"side": "buy", "ticker": "KODEX 200", "amount": 80_000, "trigger": "rebalance"},
        "expected": "approve",
        "severity": "low",
    },
}
