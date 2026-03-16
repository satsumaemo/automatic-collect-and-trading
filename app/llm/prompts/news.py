"""
뉴스 동향 분석 + 개별 감성분석 프롬프트.
"""

NEWS_ANALYSIS_PROMPT = """당신은 전문 금융 애널리스트입니다. 아래 오늘의 뉴스 데이터를 분석하여 시장 동향을 판단해주세요.

## 입력 데이터
{news_summary_json}

## 분석 요청
1. 전체 시장 분위기: 현재 시장의 전반적 톤 (낙관/중립/비관)
2. 섹터별 자금 쏠림 방향: 어떤 섹터에 관심이 집중되고 있는가
3. 주요 리스크 이벤트: 향후 1~2주 내 시장에 영향을 줄 수 있는 이벤트
4. 뉴스 톤 변화: 지난 주 대비 뉴스 톤이 어떻게 변했는가

## 중요 규칙
- 뉴스에서 직접 확인된 내용만 분석하세요. 없는 정보를 만들지 마세요.
- sector_flows의 strength는 뉴스 빈도와 톤을 기반으로 판단하세요.

## 출력 형식 (JSON만)
{{
  "market_tone": "bullish|neutral|bearish",
  "market_tone_confidence": 0.0,
  "sector_flows": [
    {{"sector": "섹터명", "direction": "inflow|outflow|neutral", "strength": 0.0, "reasoning": "근거"}}
  ],
  "risk_events": [
    {{"event": "이벤트", "expected_date": "YYYY-MM-DD 또는 unknown", "impact": "high|medium|low", "direction": "positive|negative|uncertain"}}
  ],
  "tone_shift": {{
    "direction": "improving|stable|deteriorating",
    "magnitude": 0.0,
    "key_driver": "변화의 주요 원인"
  }}
}}"""


SENTIMENT_PROMPT = """아래 금융 뉴스의 시장 영향을 -1(매우 부정)에서 +1(매우 긍정) 사이로 평가하세요.

제목: {title}
요약: {summary}

반드시 JSON만 응답: {{"sentiment": 0.0, "reasoning": "한줄 설명"}}"""
