"""DB 데이터 → LLM 시스템 프롬프트 변환"""

from app.chat.db_reader import DBReader


TOPIC_KEYWORDS = {
    "sector_tech": ["반도체", "AI", "테크", "엔비디아", "GPU", "HBM", "삼성전자", "SK하이닉스"],
    "sector_energy": ["에너지", "배터리", "2차전지", "전기차", "태양광", "유가", "원유"],
    "sector_finance": ["금융", "은행", "보험", "증권", "밸류업", "배당"],
    "macro_monetary": ["금리", "연준", "FOMC", "금통위", "인플레이션", "CPI", "통화정책"],
    "geopolitical": ["관세", "전쟁", "미중", "중동", "제재", "선거"],
    "earnings": ["실적", "어닝", "매출", "영업이익", "서프라이즈"],
    "market_sentiment": ["급등", "급락", "폭락", "랠리", "패닉", "버블"],
}

SYSTEM_PROMPT_TEMPLATE = """
당신은 데이터 기반 투자 토론 파트너입니다.
아래는 자동매매 시스템이 수집한 실제 시장 데이터입니다.
이 데이터를 바탕으로 사용자와 투자 아이디어를 토론하세요.

## 역할 지침
- 수집된 실제 데이터를 바탕으로 근거 있는 의견 제시
- 사용자의 투자 아이디어에 건설적으로 반론/보강
- 단정적 매매 추천은 하지 않고, 다양한 시각 제공
- 데이터에 없는 내용은 "현재 수집된 데이터에는 없지만"이라고 명시
- 답변은 한국어로, 핵심을 간결하게 + 근거 함께 제시

## 현재 시장 레짐
{regime_section}

## 글로벌 지표
{indicators_section}

## 최근 주요 뉴스 (2일간)
{news_section}

## 뉴스 버즈 급등 카테고리
{buzz_section}

## 최근 LLM 분석 결과
{analysis_section}

## 포트폴리오 현황
{portfolio_section}

## 최근 거래 이력 (7일)
{trades_section}
""".strip()


class ContextBuilder:
    def __init__(self, db: DBReader):
        self.db = db

    # ── 시스템 프롬프트 ──

    def build_system_prompt(self) -> str:
        return SYSTEM_PROMPT_TEMPLATE.format(
            regime_section=self._build_regime(),
            indicators_section=self._build_indicators(),
            news_section=self._build_news(),
            buzz_section=self._build_buzz(),
            analysis_section=self._build_analysis(),
            portfolio_section=self._build_portfolio(),
            trades_section=self._build_trades(),
        )

    # ── 토픽 컨텍스트 ──

    def detect_topics(self, user_message: str) -> list[str]:
        detected = []
        for category, keywords in TOPIC_KEYWORDS.items():
            if any(kw in user_message for kw in keywords):
                detected.append(category)
        return detected

    def build_topic_context(self, user_message: str) -> str:
        topics = self.detect_topics(user_message)
        if not topics:
            return ""

        parts: list[str] = []
        for topic in topics:
            try:
                news = self.db.get_news_by_category(topic, days=7, limit=10)
            except Exception:
                continue
            if news:
                parts.append(f"\n## {topic} 관련 최근 뉴스 (7일)")
                for n in news:
                    sentiment = n.get("sentiment_score")
                    sent_str = f"{sentiment:+.2f}" if sentiment is not None else "N/A"
                    parts.append(f"- [{n.get('source', '')}] {n.get('title', '')} (감성: {sent_str})")
        return "\n".join(parts)

    # ── 섹션 빌더 ──

    def _build_regime(self) -> str:
        try:
            r = self.db.get_latest_regime()
            confidence = r.get("confidence", 0)
            if isinstance(confidence, (int, float)) and confidence <= 1:
                confidence_str = f"{confidence:.0%}"
            else:
                confidence_str = f"{confidence}%"
            text = f"현재 레짐: {r.get('regime', 'unknown')} (확신도 {confidence_str})"
            if r.get("reasoning"):
                text += f"\n근거: {r['reasoning']}"
            alloc = r.get("asset_allocation_suggestion", {})
            if alloc:
                items = [f"{k} {v}%" for k, v in alloc.items()]
                text += f"\n자산배분 제안: {' | '.join(items)}"
            return text
        except Exception:
            return "데이터 없음"

    def _build_indicators(self) -> str:
        try:
            indicators = self.db.get_market_indicators()
            if not indicators:
                return "데이터 없음"
            items = [f"{ind['name']}: {ind['value']}" for ind in indicators]
            return " | ".join(items)
        except Exception:
            return "데이터 없음"

    def _build_news(self) -> str:
        try:
            news = self.db.get_recent_news(days=2, limit=15)
            if not news:
                return "데이터 없음"
            lines = []
            for n in news:
                sentiment = n.get("sentiment_score")
                sent_str = f"{sentiment:+.2f}" if sentiment is not None else "N/A"
                lines.append(f"- [{n.get('source', '')}] {n.get('title', '')} (감성: {sent_str})")
            return "\n".join(lines)
        except Exception:
            return "데이터 없음"

    def _build_buzz(self) -> str:
        try:
            buzz = self.db.get_news_buzz()
            if not buzz:
                return "데이터 없음"
            lines = []
            for b in buzz:
                avg_s = b.get("avg_sentiment")
                sent_str = f"{avg_s:+.2f}" if avg_s is not None else "N/A"
                lines.append(
                    f"- {b['category']} 버즈 {float(b['buzz_score']):.1f}배, "
                    f"감성 {sent_str}"
                )
            return "\n".join(lines)
        except Exception:
            return "데이터 없음"

    def _build_analysis(self) -> str:
        try:
            analyses = self.db.get_latest_analyses(limit=5)
            if not analyses:
                return "데이터 없음"
            lines = []
            for a in analyses:
                ts = a.get("timestamp", "")
                task = a.get("task_type", "")
                po = a.get("parsed_output", {})
                summary = ""
                if isinstance(po, dict):
                    summary = po.get("summary", po.get("reasoning", str(po)[:200]))
                lines.append(f"- [{task}] {ts}: {summary}")
            return "\n".join(lines)
        except Exception:
            return "데이터 없음"

    def _build_portfolio(self) -> str:
        try:
            data = self.db.get_current_positions()
            positions = data.get("positions", [])
            cash = data.get("cash", 0)
            total_eval = data.get("total_eval", 0)

            lines = []
            if positions:
                for p in positions:
                    ticker = p.get("ticker", "?")
                    name = p.get("name", "")
                    display = f"{name}({ticker})" if name else ticker
                    qty = p.get("quantity", "")
                    pnl = p.get("pnl_pct", 0)
                    current_price = p.get("current_price", 0)
                    lines.append(
                        f"- {display}: 수량 {qty}, 현재가 {current_price:,.0f}원, "
                        f"수익률 {pnl:+.1f}%"
                    )
            else:
                lines.append("현재 보유 종목 없음")

            if cash:
                lines.append(f"예수금: {cash:,.0f}원")
            if total_eval:
                lines.append(f"총 평가금액: {total_eval:,.0f}원")

            return "\n".join(lines)
        except Exception:
            return "데이터 없음"

    def _build_trades(self) -> str:
        try:
            trades = self.db.get_trade_history(days=7, limit=20)
            if not trades:
                return "거래 없음"
            lines = []
            for t in trades:
                date = t.get("date", "")
                ticker = t.get("ticker", "")
                side = t.get("side", "")
                qty = t.get("quantity", "")
                price = t.get("price", "")
                lines.append(f"- {date} {side} {ticker} {qty}주 @ {price}")
            return "\n".join(lines)
        except Exception:
            return "데이터 없음"
