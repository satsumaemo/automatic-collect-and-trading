"""
SignalService — Layer 3: 신호 생성 (수렴 전략).

단기 자금 쏠림 + 중장기 구조적 성장 → 수렴 판단 엔진.
- 단기 신호: "지금 돈이 어디로 몰리고 있는가" (모멘텀)
- 중장기 신호: "구조적으로 성장이 뒷받침되는가" (가치)
- 수렴: 두 신호가 같은 방향이면 매수 확신도 상승

의존성: DataService, AnalysisService
"""

import logging
from datetime import datetime, date
from typing import List, Optional, Tuple

from sqlalchemy import text

from app.models.contracts import (
    AlertLevel,
    ConvergenceResult,
    ConvergenceType,
    Regime,
    RegimeAnalysis,
    TradingSignals,
    REGIME_ALLOCATION,
    SECTOR_ETF_MAPPING,
)
from app.utils.db import get_session

logger = logging.getLogger(__name__)


class SignalService:
    """섹터 수렴 판단 및 거래 신호 생성"""

    # ── 수렴 임계값 ──
    SHORT_TERM_THRESHOLD = 60    # 단기 점수 60 이상 = 쏠림 감지
    LONG_TERM_THRESHOLD = 65     # 중장기 점수 65 이상 = 구조적 성장
    CONVERGENCE_THRESHOLD = 55   # 양쪽 55 이상 = 수렴

    # ── 레짐별 신호 강도 배율 ──
    REGIME_MULTIPLIERS = {
        "expansion": 1.0,   # 확장기: 그대로
        "slowdown": 0.7,    # 둔화기: 30% 축소
        "warning": 0.3,     # 경고기: 70% 축소
        "crisis": 0.0,      # 위기기: 수렴 무시, 전량 방어
    }

    # ── 관심 섹터 ──
    WATCHED_SECTORS = [
        "ai_semiconductor",
        "clean_energy",
        "bio_healthcare",
        "finance_valueup",
        "defense",
    ]

    # ── 섹터별 중장기 기본 점수 (Phase 3에서 재무 기반으로 교체) ──
    SECTOR_BASE_SCORES = {
        "ai_semiconductor": 75,   # AI/반도체: 구조적 성장 강함
        "clean_energy": 65,       # 클린에너지: 정책 수혜
        "bio_healthcare": 60,     # 바이오: 고령화 수혜
        "finance_valueup": 55,    # 금융: 밸류업 정책
        "defense": 60,            # 방산: 지정학 수혜
    }

    def __init__(
        self,
        data_service: "DataService",          # noqa: F821
        analysis_service: "AnalysisService",  # noqa: F821
    ) -> None:
        self.data = data_service
        self.analysis = analysis_service
        # 오케스트레이터에서 주입하는 레짐 정보
        self._last_regime: str = "slowdown"
        self._last_alert_level: str = "normal"
        # 최근 LLM 섹터 전망 (analyze_regime 결과에서 추출)
        self._sector_outlook: dict = {}
        logger.info("SignalService 초기화")

    # ── 외부 주입 ──

    def set_regime(self, regime_str: str) -> None:
        """오케스트레이터에서 레짐 판단 후 주입"""
        self._last_regime = regime_str

    def set_alert_level(self, alert_level_str: str) -> None:
        """오케스트레이터에서 경고 레벨 주입"""
        self._last_alert_level = alert_level_str

    def set_sector_outlook(self, outlook: List[dict]) -> None:
        """RegimeAnalysis의 sector_outlook을 주입"""
        self._sector_outlook = {}
        for item in outlook:
            sector = item.get("sector", "")
            self._sector_outlook[sector] = item

    # ═══════════════════════════════════════
    # 단기 자금흐름 점수 (0~100)
    # ═══════════════════════════════════════

    async def calculate_short_term_flow(self, sector: str) -> float:
        """
        단기 쏠림 점수.
        가중치: 뉴스 버즈 20% | ETF 유입 30% | 외국인 순매수 25% | 거래량 15% | LLM 10%
        """
        score = 0.0

        # 1. 뉴스 빈도 변화 (20%)
        try:
            buzz = await self.data.get_news_buzz(sector)
            if buzz and buzz.buzz_score > 0:
                buzz_pts = self._score_buzz(buzz.buzz_score)
                score += 0.20 * buzz_pts
        except Exception as e:
            logger.debug("뉴스 버즈 조회 실패 [%s]: %s", sector, e)

        # 2. ETF 자금 유입 (30%)
        try:
            flow = await self.data.get_etf_flows(sector, days=7)
            if flow:
                flow_pts = self._score_flow_ratio(flow.flow_ratio)
                score += 0.30 * flow_pts
        except Exception as e:
            logger.debug("ETF 흐름 조회 실패 [%s]: %s", sector, e)

        # 3. 외국인 순매수 (25%)
        try:
            sector_etfs = SECTOR_ETF_MAPPING.get(sector, {}).get("kr_etfs", [])
            if sector_etfs:
                supply = await self.data.get_supply_demand(sector_etfs[0], days=7)
                if supply:
                    if supply.foreign_net > 0:
                        score += 0.25 * 70  # 순매수 → 높은 점수
                    elif supply.foreign_net == 0:
                        score += 0.25 * 50
                    else:
                        score += 0.25 * 20  # 순매도 → 낮은 점수
        except Exception as e:
            logger.debug("수급 조회 실패 [%s]: %s", sector, e)

        # 4. 거래량 증가 (15%) — 기본값 50 (상세 구현은 Phase 3)
        score += 0.15 * 50

        # 5. LLM 섹터 평가 (10%)
        llm_pts = self._get_llm_sector_score(sector)
        score += 0.10 * llm_pts

        return min(round(score, 2), 100.0)

    @staticmethod
    def _score_buzz(buzz_score: float) -> float:
        """buzz_score → 0~100 점수 변환"""
        if buzz_score > 3.0:
            return 100
        elif buzz_score > 2.0:
            return 70
        elif buzz_score > 1.5:
            return 40
        elif buzz_score > 1.0:
            return 10
        return 0

    @staticmethod
    def _score_flow_ratio(ratio: float) -> float:
        """flow_ratio → 0~100 점수 변환"""
        if ratio > 2.0:
            return 100
        elif ratio > 1.5:
            return 70
        elif ratio > 1.0:
            return 40
        elif ratio > 0.5:
            return 10
        return 0

    def _get_llm_sector_score(self, sector: str) -> float:
        """LLM sector_outlook에서 점수 추출"""
        outlook = self._sector_outlook.get(sector, {})
        direction = outlook.get("outlook", "neutral")
        if direction == "overweight":
            return 80
        elif direction == "neutral":
            return 50
        elif direction == "underweight":
            return 20
        return 50

    # ═══════════════════════════════════════
    # 중장기 구조적 성장 점수 (0~100)
    # ═══════════════════════════════════════

    async def calculate_long_term_trend(self, sector: str) -> float:
        """
        중장기 구조적 성장 점수.
        현재는 기본 점수 + LLM 전망 보정.
        Phase 3에서 재무 데이터 기반으로 고도화.
        """
        base = self.SECTOR_BASE_SCORES.get(sector, 50)

        # LLM 전망으로 ±15점 보정
        llm_adj = 0
        outlook = self._sector_outlook.get(sector, {})
        direction = outlook.get("outlook", "neutral")
        if direction == "overweight":
            llm_adj = 15
        elif direction == "underweight":
            llm_adj = -15

        return min(max(base + llm_adj, 0), 100.0)

    # ═══════════════════════════════════════
    # 수렴 판단
    # ═══════════════════════════════════════

    async def evaluate_convergence(self) -> List[ConvergenceResult]:
        """모든 관심 섹터의 수렴 상태 평가"""
        results: List[ConvergenceResult] = []

        for sector in self.WATCHED_SECTORS:
            try:
                short_score = await self.calculate_short_term_flow(sector)
                long_score = await self.calculate_long_term_trend(sector)
                conv_type, confidence, multiplier = self._determine_convergence(
                    short_score, long_score
                )

                # 추천 ETF 결정
                sector_info = SECTOR_ETF_MAPPING.get(sector, {})
                if conv_type in (ConvergenceType.STRONG, ConvergenceType.WEAK):
                    recommended = sector_info.get("kr_etfs", []) + sector_info.get("us_etfs", [])
                else:
                    recommended = []

                results.append(ConvergenceResult(
                    sector=sector,
                    short_term_score=round(short_score, 2),
                    long_term_score=round(long_score, 2),
                    convergence_type=conv_type,
                    confidence=round(confidence, 3),
                    recommended_etfs=recommended,
                    position_multiplier=multiplier,
                ))

            except Exception as e:
                logger.error("섹터 수렴 판단 실패 [%s]: %s", sector, e)

        # 확신도 내림차순 정렬
        results.sort(key=lambda x: x.confidence, reverse=True)
        return results

    def _determine_convergence(
        self, short: float, long: float
    ) -> Tuple[ConvergenceType, float, float]:
        """수렴 유형 판단 → (type, confidence, position_multiplier)"""
        if short >= self.SHORT_TERM_THRESHOLD and long >= self.LONG_TERM_THRESHOLD:
            return ConvergenceType.STRONG, min(short, long) / 100, 1.5

        if short >= self.CONVERGENCE_THRESHOLD and long >= self.CONVERGENCE_THRESHOLD:
            return ConvergenceType.WEAK, (short + long) / 200, 1.0

        if short >= self.SHORT_TERM_THRESHOLD and long < self.CONVERGENCE_THRESHOLD:
            return ConvergenceType.SHORT_ONLY, 0.3, 0.3

        if long >= self.LONG_TERM_THRESHOLD and short < self.CONVERGENCE_THRESHOLD:
            return ConvergenceType.LONG_ONLY, 0.4, 0.5

        return ConvergenceType.NONE, 0.2, 0.0

    # ═══════════════════════════════════════
    # 최종 신호 패키지
    # ═══════════════════════════════════════

    async def generate_signals(self) -> TradingSignals:
        """최종 거래 신호 패키지 생성"""
        # 1. 수렴 판단
        convergence_results = await self.evaluate_convergence()

        # 2. 레짐 필터 적용
        regime_mult = self.REGIME_MULTIPLIERS.get(self._last_regime, 0.7)
        for result in convergence_results:
            result.confidence = round(result.confidence * regime_mult, 3)
            result.position_multiplier = round(result.position_multiplier * regime_mult, 2)

        # 3. signal_scores DB 저장
        await self._save_signal_scores(convergence_results)

        # 4. TradingSignals 패키지 구성
        try:
            regime_enum = Regime(self._last_regime)
        except ValueError:
            regime_enum = Regime.SLOWDOWN

        try:
            alert_enum = AlertLevel(self._last_alert_level)
        except ValueError:
            alert_enum = AlertLevel.NORMAL

        regime_analysis = RegimeAnalysis(
            regime=regime_enum,
            confidence=0.5,
            reasoning="SignalService에서 생성한 레짐 참조",
            asset_allocation_suggestion=REGIME_ALLOCATION.get(regime_enum, {}),
            sector_outlook=list(self._sector_outlook.values()),
            key_risks=[],
        )

        return TradingSignals(
            date=datetime.now(),
            regime=regime_analysis,
            convergence_results=convergence_results,
            alert_level=alert_enum,
            default_etfs=["KODEX 200", "TIGER 미국S&P500"],
        )

    async def _save_signal_scores(self, results: List[ConvergenceResult]) -> None:
        """signal_scores 테이블에 저장"""
        today = date.today()
        try:
            async with get_session() as session:
                for r in results:
                    await session.execute(
                        text("""
                            INSERT INTO signal_scores
                                (date, sector, short_term_score, long_term_score,
                                 convergence_type, adjusted_confidence, market_regime,
                                 recommended_action)
                            VALUES
                                (:dt, :sector, :short, :long,
                                 :conv, :conf, :regime, :action)
                            ON CONFLICT (date, sector) DO UPDATE SET
                                short_term_score = EXCLUDED.short_term_score,
                                long_term_score = EXCLUDED.long_term_score,
                                convergence_type = EXCLUDED.convergence_type,
                                adjusted_confidence = EXCLUDED.adjusted_confidence,
                                market_regime = EXCLUDED.market_regime,
                                recommended_action = EXCLUDED.recommended_action
                        """),
                        {
                            "dt": today,
                            "sector": r.sector,
                            "short": r.short_term_score,
                            "long": r.long_term_score,
                            "conv": r.convergence_type.value,
                            "conf": r.confidence,
                            "regime": self._last_regime,
                            "action": (
                                "buy" if r.convergence_type in (ConvergenceType.STRONG, ConvergenceType.WEAK)
                                else "hold" if r.convergence_type == ConvergenceType.LONG_ONLY
                                else "wait"
                            ),
                        },
                    )
                    logger.info(
                        "신호: %s | 단기=%.1f 장기=%.1f | %s | 확신도=%.3f",
                        r.sector, r.short_term_score, r.long_term_score,
                        r.convergence_type.value, r.confidence,
                    )
        except Exception as e:
            logger.error("signal_scores 저장 실패: %s", e)
