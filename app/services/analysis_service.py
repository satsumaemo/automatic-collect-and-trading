"""
AnalysisService — LLM 기반 분석 서비스.

역할: Gemini LLM을 활용하여 시장 레짐 판단, 뉴스 분석, 위험 감지,
      거래 사전검증, 사후 리뷰 등 AI 분석을 수행합니다.
의존성: DataService, LLMManager, HallucinationGuard
"""

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import text

from app.llm.hallucination_guard import HallucinationGuard
from app.llm.llm_manager import LLMManager
from app.llm.prompts.news import NEWS_ANALYSIS_PROMPT, SENTIMENT_PROMPT
from app.llm.prompts.regime import MACRO_REGIME_PROMPT
from app.llm.prompts.review import (
    EMERGENCY_ANALYSIS_PROMPT,
    FINANCIAL_ANALYSIS_PROMPT,
    POST_TRADE_REVIEW_PROMPT,
)
from app.llm.prompts.risk import RISK_DETECTION_PROMPT
from app.llm.prompts.validation import PRE_TRADE_VALIDATION_PROMPT
from app.models.contracts import (
    AlertLevel,
    FinancialAnalysis,
    NewsAnalysis,
    OrderTrigger,
    PostTradeReview,
    ProposedOrder,
    Regime,
    RegimeAnalysis,
    RiskDetection,
    SentimentResult,
    ValidationResult,
)
from app.utils.db import get_session

logger = logging.getLogger(__name__)

# LLM 검증 타임아웃 (초)
VALIDATION_TIMEOUT = 5

# 감성분석 배치 최대 건수
MAX_SENTIMENT_BATCH = 50


def _safe_json(obj, default: str = "{}") -> str:
    """dataclass/dict를 JSON 문자열로 안전하게 변환"""
    if obj is None:
        return default
    try:
        if hasattr(obj, "__dataclass_fields__"):
            return json.dumps(asdict(obj), default=str, ensure_ascii=False)
        return json.dumps(obj, default=str, ensure_ascii=False)
    except Exception:
        return default


class AnalysisService:
    """LLM 기반 시장 분석 및 검증"""

    def __init__(self, data_service: "DataService") -> None:  # noqa: F821
        self.data = data_service
        self.llm = LLMManager()
        self.guard = HallucinationGuard()
        logger.info("AnalysisService 초기화")

    # ═══════════════════════════════════════
    # 시장 레짐 판단
    # ═══════════════════════════════════════

    async def analyze_regime(self) -> RegimeAnalysis:
        """시장 레짐 판단 (LLM) — 매크로 지표 + 뉴스 종합 분석"""
        # 1. 데이터 수집
        macro = None
        news = None
        events = []

        try:
            macro = await self.data.get_macro_snapshot()
        except Exception as e:
            logger.warning("매크로 스냅샷 조회 실패: %s", e)

        try:
            news = await self.data.get_news_summary()
        except Exception as e:
            logger.warning("뉴스 요약 조회 실패: %s", e)

        try:
            events = await self.data.get_economic_events(days=7)
        except Exception as e:
            logger.warning("경제 이벤트 조회 실패: %s", e)

        # 2. 프롬프트 조립
        prompt = MACRO_REGIME_PROMPT.format(
            macro_indicators_json=_safe_json(macro),
            news_summary_json=_safe_json(news),
            upcoming_events_json=(
                json.dumps([asdict(e) for e in events], default=str, ensure_ascii=False)
                if events
                else "[]"
            ),
        )

        # 3. LLM 호출
        result = await self.llm.call("regime", prompt)

        # 4. 할루시네이션 방어
        if result and macro:
            result = await self.guard.validate_regime_output(result, macro)

        # 5. 실패 시 이전 유효 판단 폴백
        if result is None:
            logger.warning("레짐 분석 실패 — 이전 유효 판단 조회")
            result = await self.guard.get_previous_valid("regime")
            if result is None:
                logger.warning("이전 유효 판단 없음 — 기본값(둔화기) 적용")
                return RegimeAnalysis(
                    regime=Regime.SLOWDOWN,
                    confidence=0.3,
                    reasoning="LLM 분석 불가 — 기본값(둔화기) 적용",
                    asset_allocation_suggestion={
                        "kr_equity": 20, "us_equity": 25,
                        "kr_bond": 15, "us_bond": 15,
                        "gold": 10, "cash_rp": 15,
                    },
                    sector_outlook=[],
                    key_risks=[],
                )

        # 6. dict → RegimeAnalysis 변환
        try:
            regime_val = result.get("regime", "slowdown")
            return RegimeAnalysis(
                regime=Regime(regime_val),
                confidence=float(result.get("regime_confidence", 0.5)),
                reasoning=result.get("regime_reasoning", ""),
                asset_allocation_suggestion=result.get("asset_allocation_suggestion", {}),
                sector_outlook=result.get("sector_outlook", []),
                key_risks=result.get("key_macro_risks", []),
            )
        except (ValueError, KeyError) as e:
            logger.error("레짐 결과 변환 실패: %s", e)
            return RegimeAnalysis(
                regime=Regime.SLOWDOWN,
                confidence=0.3,
                reasoning=f"결과 변환 실패: {e}",
                asset_allocation_suggestion={
                    "kr_equity": 20, "us_equity": 25,
                    "kr_bond": 15, "us_bond": 15,
                    "gold": 10, "cash_rp": 15,
                },
                sector_outlook=[],
                key_risks=[],
            )

    # ═══════════════════════════════════════
    # 뉴스 동향 분석
    # ═══════════════════════════════════════

    async def analyze_news_overview(self) -> NewsAnalysis:
        """뉴스 동향 종합 분석 (LLM)"""
        # 데이터 수집
        news = None
        try:
            news = await self.data.get_news_summary()
        except Exception as e:
            logger.warning("뉴스 요약 조회 실패: %s", e)

        prompt = NEWS_ANALYSIS_PROMPT.format(
            news_summary_json=_safe_json(news),
        )

        result = await self.llm.call("news_overview", prompt)

        if result is None:
            logger.warning("뉴스 분석 실패 — 기본값 반환")
            return NewsAnalysis(
                market_tone="neutral",
                tone_confidence=0.3,
                sector_flows=[],
                risk_events=[],
                tone_shift={"direction": "stable", "magnitude": 0, "key_driver": "분석 불가"},
            )

        return NewsAnalysis(
            market_tone=result.get("market_tone", "neutral"),
            tone_confidence=float(result.get("market_tone_confidence", 0.5)),
            sector_flows=result.get("sector_flows", []),
            risk_events=result.get("risk_events", []),
            tone_shift=result.get("tone_shift", {}),
        )

    # ═══════════════════════════════════════
    # 개별 뉴스 감성분석 (배치)
    # ═══════════════════════════════════════

    async def analyze_sentiments(self, articles: List[dict]) -> List[SentimentResult]:
        """
        개별 뉴스 감성분석 배치 (LLM flash-lite 모델).
        최대 MAX_SENTIMENT_BATCH건까지 처리.
        """
        # 상위 N건만 처리
        target = articles[:MAX_SENTIMENT_BATCH]
        results: List[SentimentResult] = []

        for article in target:
            article_id = article.get("article_id", 0)
            title = article.get("title", "")
            summary = article.get("summary", "")

            if not title:
                continue

            try:
                prompt = SENTIMENT_PROMPT.format(
                    title=title,
                    summary=summary[:300],
                )
                llm_result = await self.llm.call("sentiment", prompt, max_tokens=256)

                if llm_result:
                    sentiment_val = float(llm_result.get("sentiment", 0))
                    sentiment_val = max(-1.0, min(1.0, sentiment_val))
                    reasoning = llm_result.get("reasoning", "")
                else:
                    sentiment_val = 0.0
                    reasoning = "LLM 응답 없음"

                results.append(SentimentResult(
                    article_id=article_id,
                    sentiment=sentiment_val,
                    reasoning=reasoning,
                ))

                # DB 업데이트
                await self._update_article_sentiment(article_id, sentiment_val)

            except Exception as e:
                logger.error("감성분석 실패 [article_id=%d]: %s", article_id, e)
                results.append(SentimentResult(
                    article_id=article_id, sentiment=0.0, reasoning=f"오류: {e}"
                ))

        logger.info("감성분석 완료: %d/%d건", len(results), len(target))
        return results

    async def _update_article_sentiment(self, article_id: int, sentiment: float) -> None:
        """news_articles 테이블의 sentiment_score 업데이트"""
        try:
            async with get_session() as session:
                session.execute(
                    text("""
                        UPDATE news_articles
                        SET sentiment_score = :score, is_processed = TRUE
                        WHERE article_id = :aid
                    """),
                    {"score": sentiment, "aid": article_id},
                )
        except Exception as e:
            logger.error("감성 점수 DB 업데이트 실패 [%d]: %s", article_id, e)

    # ═══════════════════════════════════════
    # 재무제표 분석
    # ═══════════════════════════════════════

    async def analyze_financials(self, company_id: int) -> FinancialAnalysis:
        """재무제표 분석 (LLM)"""
        # 데이터 조회
        try:
            fin_data = await self.data.get_financial_data(company_id)
        except Exception as e:
            logger.error("재무 데이터 조회 실패 [%d]: %s", company_id, e)
            return FinancialAnalysis(
                company_id=company_id, overall_score=50,
                investment_grade="C", key_risks=["데이터 조회 실패"],
                key_strengths=[], confidence=0.0,
            )

        prompt = FINANCIAL_ANALYSIS_PROMPT.format(
            financial_data_json=_safe_json(fin_data),
        )

        result = await self.llm.call("financial", prompt)

        if result is None:
            return FinancialAnalysis(
                company_id=company_id, overall_score=50,
                investment_grade="C", key_risks=["LLM 분석 실패"],
                key_strengths=[], confidence=0.0,
            )

        return FinancialAnalysis(
            company_id=company_id,
            overall_score=int(result.get("overall_score", 50)),
            investment_grade=result.get("investment_grade", "C"),
            key_risks=result.get("key_risks", []),
            key_strengths=result.get("key_strengths", []),
            confidence=float(result.get("confidence", 0.5)),
        )

    # ═══════════════════════════════════════
    # 위험 감지
    # ═══════════════════════════════════════

    async def detect_risks(self) -> RiskDetection:
        """위험 감지 (LLM) — 매크로 + 시장 신호 종합"""
        # 데이터 수집
        macro = None
        try:
            macro = await self.data.get_macro_snapshot()
        except Exception as e:
            logger.warning("매크로 스냅샷 조회 실패: %s", e)

        # 이상 징후 데이터 구성
        anomaly_data: Dict = {}
        if macro:
            anomaly_data["vix"] = macro.vix
            anomaly_data["yield_spread"] = macro.yield_spread
            anomaly_data["hy_spread"] = macro.hy_spread
            anomaly_data["hy_spread_percentile"] = macro.hy_spread_percentile
            if macro.fsi is not None:
                anomaly_data["fsi"] = macro.fsi

        prompt = RISK_DETECTION_PROMPT.format(
            risk_indicators_json=_safe_json(macro),
            anomaly_data_json=json.dumps(anomaly_data, default=str, ensure_ascii=False),
        )

        result = await self.llm.call("risk_detection", prompt)

        # 할루시네이션 방어
        if result:
            result = await self.guard.validate_risk_output(result)

        # 실패 시 폴백
        if result is None:
            prev = await self.guard.get_previous_valid("risk_detection")
            if prev:
                result = prev
            else:
                return RiskDetection(
                    alert_level=AlertLevel.NORMAL,
                    confidence=0.3,
                    detected_signals=[],
                    recommended_actions=[],
                )

        try:
            return RiskDetection(
                alert_level=AlertLevel(result.get("alert_level", "normal")),
                confidence=float(result.get("alert_confidence", 0.5)),
                detected_signals=result.get("detected_signals", []),
                recommended_actions=result.get("recommended_actions", []),
            )
        except (ValueError, KeyError) as e:
            logger.error("리스크 결과 변환 실패: %s", e)
            return RiskDetection(
                alert_level=AlertLevel.NORMAL,
                confidence=0.3,
                detected_signals=[],
                recommended_actions=[],
            )

    # ═══════════════════════════════════════
    # 긴급 분석
    # ═══════════════════════════════════════

    async def emergency_analysis(self, trigger: str, context: dict) -> dict:
        """긴급 분석 (LLM) — 비상 상황 발생 시 즉각 대응"""
        prompt = EMERGENCY_ANALYSIS_PROMPT.format(
            trigger=trigger,
            context_json=json.dumps(context, default=str, ensure_ascii=False),
        )

        result = await self.llm.call("emergency", prompt, max_tokens=1024)

        if result is None:
            return {
                "severity": "high",
                "assessment": f"LLM 분석 불가 — 트리거: {trigger}",
                "recommended_actions": ["포지션 동결", "수동 확인 필요"],
                "position_adjustment": "hold",
            }

        return result

    # ═══════════════════════════════════════
    # 거래 사전검증 (AI 검문소)
    # ═══════════════════════════════════════

    async def validate_trade(
        self, order: ProposedOrder, system_state: dict
    ) -> ValidationResult:
        """
        거래 사전검증 (LLM) — AI 검문소, 5개 항목 체크.
        손절/비상/Kill Switch 주문은 면제.
        5초 타임아웃.
        """
        # 면제 조건 확인
        exempt_triggers = {OrderTrigger.STOP_LOSS, OrderTrigger.EMERGENCY, OrderTrigger.KILL_SWITCH}
        if order.trigger in exempt_triggers:
            return ValidationResult(
                decision="approve", confidence=1.0,
                checks={}, size_reduction_pct=100,
                risk_summary="손절/비상 면제",
            )

        # 극소액 면제 (10만원 미만)
        if order.amount < 100_000:
            return ValidationResult(
                decision="approve", confidence=1.0,
                checks={}, size_reduction_pct=100,
                risk_summary="극소액 면제",
            )

        # 프롬프트 구성
        trade_info = {
            "ticker": order.ticker,
            "side": order.side.value,
            "quantity": order.quantity,
            "price": order.price,
            "amount": order.amount,
            "trigger": order.trigger.value,
            "reason": order.reason,
            "sector": order.sector,
        }

        prompt = PRE_TRADE_VALIDATION_PROMPT.format(
            trade_order_json=json.dumps(trade_info, ensure_ascii=False),
            current_regime=system_state.get("regime", "unknown"),
            alert_level=system_state.get("alert_level", "normal"),
            portfolio_summary=system_state.get("portfolio_summary", "{}"),
            daily_trades_summary=system_state.get("daily_trades", "[]"),
            morning_analysis_summary=system_state.get("morning_analysis", "분석 없음"),
            recent_news_summary=system_state.get("recent_news", "뉴스 없음"),
            upcoming_events=system_state.get("upcoming_events", "[]"),
        )

        # LLM 호출 (타임아웃)
        try:
            result = await asyncio.wait_for(
                self.llm.call("validation", prompt),
                timeout=VALIDATION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("LLM 검증 타임아웃 (%ds) — 안전 모드(50%% 축소) 적용", VALIDATION_TIMEOUT)
            return ValidationResult(
                decision="conditional_approve", confidence=0.5,
                checks={}, size_reduction_pct=50,
                risk_summary="LLM 검증 타임아웃 — 50% 축소",
            )
        except Exception as e:
            logger.warning("LLM 검증 실패: %s — 안전 모드(50%% 축소) 적용", e)
            return ValidationResult(
                decision="conditional_approve", confidence=0.5,
                checks={}, size_reduction_pct=50,
                risk_summary=f"LLM 장애({type(e).__name__}) — 50% 축소",
            )

        # 결과 파싱 실패
        if result is None:
            return ValidationResult(
                decision="conditional_approve", confidence=0.5,
                checks={}, size_reduction_pct=50,
                risk_summary="LLM 응답 파싱 실패 — 50% 축소",
            )

        # 할루시네이션 방어
        validated = await self.guard.validate_validation_output(result)
        if validated is None:
            return ValidationResult(
                decision="conditional_approve", confidence=0.5,
                checks={}, size_reduction_pct=50,
                risk_summary="LLM 응답 검증 실패 — 50% 축소",
            )

        # ValidationResult 변환
        modification = validated.get("modification", {})
        return ValidationResult(
            decision=validated.get("decision", "conditional_approve"),
            confidence=float(validated.get("confidence", 0.5)),
            checks=validated.get("checks", {}),
            size_reduction_pct=int(modification.get("reduce_size_to_pct", 100)),
            risk_summary=validated.get("risk_summary", ""),
            defer_until=None,
        )

    # ═══════════════════════════════════════
    # 일일 거래 사후 리뷰
    # ═══════════════════════════════════════

    async def review_daily_trades(self, trades: List[dict]) -> PostTradeReview:
        """일일 거래 사후 리뷰 (LLM) — 장 마감 후 전체 거래 평가"""
        # 시장 컨텍스트
        market_context: Dict = {}
        try:
            macro = await self.data.get_macro_snapshot()
            market_context["macro"] = _safe_json(macro)
        except Exception:
            pass

        prompt = POST_TRADE_REVIEW_PROMPT.format(
            today_trades_json=json.dumps(trades, default=str, ensure_ascii=False),
            market_context_json=json.dumps(market_context, default=str, ensure_ascii=False),
        )

        result = await self.llm.call("post_review", prompt)

        if result is None:
            return PostTradeReview(
                date=datetime.now(),
                total_trades=len(trades),
                flagged_trades=[],
                overall_assessment="LLM 리뷰 불가",
                improvement_suggestions=[],
            )

        return PostTradeReview(
            date=datetime.now(),
            total_trades=int(result.get("total_trades", len(trades))),
            flagged_trades=result.get("flagged_trades", []),
            overall_assessment=result.get("overall_assessment", ""),
            improvement_suggestions=result.get("improvement_suggestions", []),
        )
