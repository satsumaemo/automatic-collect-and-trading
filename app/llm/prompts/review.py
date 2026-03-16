"""
사후 리뷰 프롬프트.
"""

POST_TRADE_REVIEW_PROMPT = """오늘 실행된 거래를 종합적으로 리뷰하세요.

## 오늘의 거래 목록
{today_trades_json}

## 시장 상황
{market_context_json}

## 리뷰 항목
1. 슬리피지 분석: 비정상적 괴리가 있었나?
2. 비용 효율성: 거래비용이 합리적이었나?
3. 전략 일관성: 거래가 전체 전략과 일관되었나?
4. 개선점: 더 나은 실행이 가능했나?

## 중요 규칙
- 실제 거래 데이터에 기반하여 분석하세요.
- flagged_trades에는 실제 문제가 있는 거래만 포함하세요.
- 거래가 없으면 total_trades: 0, 빈 리스트를 반환하세요.

## 출력 형식 (JSON만)
{{
  "total_trades": 0,
  "flagged_trades": [
    {{"trade_id": "...", "issue": "문제 설명", "severity": "low|medium|high"}}
  ],
  "overall_assessment": "전반적 평가 2~3문장",
  "improvement_suggestions": ["개선점1", "개선점2"],
  "cost_analysis": {{
    "total_commission": 0,
    "total_slippage_pct": 0.0,
    "is_acceptable": true
  }}
}}"""


FINANCIAL_ANALYSIS_PROMPT = """당신은 기업 재무 분석 전문가입니다.
아래 재무제표 데이터를 분석하여 투자 등급을 판단해주세요.

## 재무 데이터
{financial_data_json}

## 분석 요청
- 전반적 재무 건전성 점수 (0~100)
- 투자 등급 (A/B/C/D/F)
- 핵심 리스크와 강점

## 출력 형식 (JSON만)
{{
  "overall_score": 0,
  "investment_grade": "A|B|C|D|F",
  "key_risks": ["리스크1", "리스크2"],
  "key_strengths": ["강점1", "강점2"],
  "confidence": 0.0
}}"""


EMERGENCY_ANALYSIS_PROMPT = """긴급 상황이 발생했습니다. 빠르게 분석하세요.

## 트리거
{trigger}

## 상황 데이터
{context_json}

## 출력 형식 (JSON만)
{{
  "severity": "low|medium|high|critical",
  "assessment": "상황 평가 2~3문장",
  "recommended_actions": ["즉시 조치1", "즉시 조치2"],
  "position_adjustment": "hold|reduce_25|reduce_50|liquidate"
}}"""
