"""과거 위기 + 정상 확장기 시나리오 정의"""

HISTORICAL_CRISES = {
    "covid_2020_03": {
        "name": "코로나 폭락 (2020.03)",
        "market_state": {
            "vix": 82.69,
            "hy_spread_percentile": 98,
            "yield_spread": -0.05,
            "usdkrw": 1280,
            "fed_rate": 0.25,
            "cpi_latest": 1.5,
            "us10y": 0.54,
            "us2y": 0.23,
            "hy_spread": 10.87,
            "fsi": 5.4,
        },
        "news": [
            {"title": "WHO 팬데믹 선언, 전 세계 봉쇄", "sentiment": -0.9},
            {"title": "글로벌 공급망 마비, 경제 셧다운", "sentiment": -0.85},
        ],
        "expected": {
            "regime": "crisis",
            "alert_level": "emergency",
            "buy_blocked": True,
        },
    },
    "rate_hike_2022": {
        "name": "금리 인상 급락 (2022)",
        "market_state": {
            "vix": 34,
            "hy_spread_percentile": 78,
            "yield_spread": -0.50,
            "usdkrw": 1430,
            "fed_rate": 4.50,
            "cpi_latest": 7.1,
            "us10y": 4.2,
            "us2y": 4.7,
            "hy_spread": 5.5,
            "fsi": 2.1,
        },
        "news": [
            {"title": "연준 75bp 자이언트스텝, 인플레 40년 최고", "sentiment": -0.6},
            {"title": "경기침체 확률 60% 돌파", "sentiment": -0.7},
        ],
        "expected": {
            "regime": "warning",
            "alert_level": "warning",
            "buy_blocked": True,
        },
    },
    "normal_expansion": {
        "name": "정상 확장기 (매수 허용 확인)",
        "market_state": {
            "vix": 14,
            "hy_spread_percentile": 25,
            "yield_spread": 1.5,
            "usdkrw": 1300,
            "fed_rate": 4.50,
            "cpi_latest": 2.5,
            "us10y": 4.0,
            "us2y": 3.8,
            "hy_spread": 3.2,
            "fsi": -0.5,
        },
        "news": [
            {"title": "GDP 성장률 호조, 고용시장 견조", "sentiment": 0.7},
        ],
        "expected": {
            "regime": "expansion",
            "alert_level": "normal",
            "buy_blocked": False,
        },
    },
}
