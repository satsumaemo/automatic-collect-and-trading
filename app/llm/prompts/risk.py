"""
위험 신호 감지 프롬프트.
"""

RISK_DETECTION_PROMPT = """당신은 리스크 관리 전문가입니다.
현재 시장 데이터에서 비정상적 패턴이나 위기 선행 신호를 감지해주세요.

## 현재 위험 지표
{risk_indicators_json}

## 최근 이상 징후
{anomaly_data_json}

## 중요 규칙
- 데이터에 직접 나타난 이상 징후만 보고하세요.
- VIX가 20 미만이고 다른 이상 징후가 없으면 alert_level은 normal이어야 합니다.
- alert_level은 감지된 신호의 심각도와 개수에 비례해야 합니다.
- 과거 유사 사례가 확실하지 않으면 historical_precedent를 null로 설정하세요.

## 출력 형식 (JSON만)
{{
  "alert_level": "normal|caution|warning|emergency",
  "alert_confidence": 0.0,
  "detected_signals": [
    {{
      "signal_type": "credit_stress|volatility_spike|yield_inversion|liquidity_crunch|geopolitical",
      "severity": "low|medium|high|critical",
      "description": "설명",
      "historical_precedent": null
    }}
  ],
  "recommended_actions": [
    {{
      "action": "reduce_equity|increase_cash|add_hedge|no_action",
      "urgency": "immediate|within_day|within_week",
      "magnitude": "10%|25%|50%|full"
    }}
  ],
  "stress_scenario": {{
    "worst_case_drawdown": "-10%",
    "probability": 0.0,
    "timeframe": "1w|1m|3m"
  }}
}}"""
