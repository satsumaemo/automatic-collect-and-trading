"""
TradingOrchestrator — 메인 오케스트레이터.

서비스들을 조립하고 파이프라인을 실행합니다.
의존성 주입: 아래에서 위로 생성합니다.

파이프라인:
  1. run_daily_pipeline()   — 07:30 메인 (9단계)
  2. run_midday_update()    — 12:30 경량 업데이트
  3. run_realtime_monitor() — 장중 5분 간격 감시
  4. run_closing_review()   — 16:30 장마감 리뷰
"""

import asyncio
import logging
from datetime import datetime

from app.config import settings
from app.services.data_service import DataService
from app.services.analysis_service import AnalysisService
from app.services.signal_service import SignalService
from app.services.portfolio_service import PortfolioService
from app.services.risk_service import RiskService
from app.services.execution_service import ExecutionService
from app.services.monitoring_service import MonitoringService
from app.llm.llm_manager import LLMManager
from app.utils.telegram import notifier
from app.utils import redis_client

logger = logging.getLogger(__name__)


class TradingOrchestrator:
    """메인 오케스트레이터 — 서비스 조립 및 파이프라인 실행"""

    def __init__(self) -> None:
        logger.info("=== TradingOrchestrator 초기화 [%s 모드] ===", settings.mode)

        # 의존성 주입: 아래에서 위로
        self.llm_manager = LLMManager()
        self.data_service = DataService()
        self.analysis_service = AnalysisService(self.data_service)
        self.signal_service = SignalService(self.data_service, self.analysis_service)
        self.portfolio_service = PortfolioService(self.signal_service)
        self.risk_service = RiskService(self.data_service, self.analysis_service)

        # ExecutionService에 DataService의 브로커 주입
        broker = getattr(self.data_service, "broker", None)
        self.execution_service = ExecutionService(broker=broker)

        self.monitoring_service = MonitoringService()

        # 이전 경고 레벨 (변경 감지용)
        self._prev_alert = None

    async def run_daily_pipeline(self) -> None:
        """
        매일 07:30 메인 파이프라인 (9단계).

        1단계: 데이터 수집 (일일 배치)
        2단계: 시장 레짐 판단 (LLM)
        3단계: 뉴스 분석 (LLM)
        4단계: 위험 감지 (LLM)
        5단계: 섹터 수렴 신호 생성
        6단계: 포트폴리오 배분 계산
        7단계: 주문 생성
        8단계: 리스크 검증 + 실행
        9단계: 모니터링 기록
        """
        logger.info("[파이프라인] 일일 메인 파이프라인 시작 — %s", datetime.now())

        # 1단계: 데이터 수집
        try:
            await self.data_service.collect_daily_batch()
            logger.info("[1/9] 데이터 수집 완료")
        except Exception as e:
            logger.error("[1/9] 데이터 수집 실패: %s", e)

        # 1.5단계: RiskService + PortfolioService 상태 주입
        try:
            positions = await redis_client.get_positions()
            cash_info = await redis_client.get_cash()
            pos_value = sum(
                float(p.get("eval_amount", 0) or p.get("value", 0))
                for p in positions.values()
            ) if positions else 0
            total_value = int(pos_value + float(cash_info.get("krw", 0)))
            self.risk_service.update_state(total_value, positions)
            self.portfolio_service.update_portfolio_state(total_value, positions, {})
        except Exception as e:
            logger.warning("[1.5] 상태 갱신 실패: %s", e)

        # 2단계: 시장 레짐 판단
        regime_analysis = None
        try:
            regime_analysis = await self.analysis_service.analyze_regime()
            logger.info("[2/9] 레짐 판단: %s (신뢰도: %.2f)",
                        regime_analysis.regime.value, regime_analysis.confidence)
            # SignalService에 레짐 + 섹터 전망 주입
            self.signal_service.set_regime(regime_analysis.regime.value)
            self.signal_service.set_sector_outlook(regime_analysis.sector_outlook)
            await redis_client.set_regime(regime_analysis.regime.value)
        except Exception as e:
            logger.error("[2/9] 레짐 판단 실패: %s", e)

        # 3단계: 뉴스 분석
        try:
            news_analysis = await self.analysis_service.analyze_news_overview()
            logger.info("[3/9] 뉴스 분석 완료 — 시장 톤: %s", news_analysis.market_tone)
        except Exception as e:
            logger.error("[3/9] 뉴스 분석 실패: %s", e)

        # 4단계: 위험 감지 + 경고 레벨
        try:
            risk_detection = await self.analysis_service.detect_risks()
            logger.info("[4/9] 위험 감지 완료 — 경고: %s", risk_detection.alert_level.value)
            self.signal_service.set_alert_level(risk_detection.alert_level.value)
            await redis_client.set_alert_level(risk_detection.alert_level.value)

            # 경고 레벨 변경 감지
            alert = self.risk_service.evaluate_alert_level()
            if self._prev_alert and alert != self._prev_alert:
                await self.monitoring_service.on_alert_level_changed(self._prev_alert, alert)
            self._prev_alert = alert
        except Exception as e:
            logger.error("[4/9] 위험 감지 실패: %s", e)

        # 5단계: 섹터 수렴 신호
        signals = None
        try:
            signals = await self.signal_service.generate_signals()
            logger.info("[5/9] 신호 생성 완료 — %d개 섹터", len(signals.convergence_results))
        except Exception as e:
            logger.error("[5/9] 신호 생성 실패: %s", e)

        # 5.5단계: 포트폴리오 목표 저장
        if regime_analysis:
            try:
                await self.portfolio_service.save_portfolio_targets(regime_analysis.regime)
            except Exception as e:
                logger.warning("[5.5] 포트폴리오 목표 저장 실패: %s", e)

        # 6단계: 주문 생성
        orders = []
        try:
            orders = self.portfolio_service.generate_orders()
            logger.info("[6/9] 주문 생성 완료 — %d건", len(orders))
        except Exception as e:
            logger.error("[6/9] 주문 생성 실패: %s", e)

        # 7~8단계: 리스크 검증 + 실행
        executed = []
        for order in orders:
            try:
                result = await self.risk_service.process_order(order)
                from app.models.contracts import ApprovedOrder
                if isinstance(result, ApprovedOrder):
                    exec_result = await self.execution_service.execute(result)
                    executed.append(exec_result)
                    await self.monitoring_service.on_trade_executed(exec_result)
                else:
                    await self.monitoring_service.on_order_rejected(result)
            except Exception as e:
                logger.error("[7-8/9] 주문 처리 실패: %s — %s", order.ticker, e)

        logger.info("[7-8/9] 실행 완료 — %d/%d건 체결", len(executed), len(orders))

        # 리밸런싱 기록
        if executed:
            self.portfolio_service.record_rebalance()

        # 9단계: 모니터링
        try:
            self.monitoring_service.log_event("daily_pipeline_complete", {
                "regime": regime_analysis.regime.value if regime_analysis else "unknown",
                "orders_total": len(orders),
                "orders_executed": len(executed),
            })
            logger.info("[9/9] 모니터링 기록 완료")
        except Exception as e:
            logger.error("[9/9] 모니터링 기록 실패: %s", e)

        logger.info("[파이프라인] 일일 메인 파이프라인 종료")

    async def run_midday_update(self) -> None:
        """12:30 경량 업데이트 — 뉴스 + 위험 재평가"""
        logger.info("[경량 업데이트] 시작 — %s", datetime.now())
        try:
            await self.data_service.collect_hourly()
            await self.analysis_service.detect_risks()
            alert_level = self.risk_service.evaluate_alert_level()
            await redis_client.set_alert_level(alert_level.value)
            logger.info("[경량 업데이트] 완료 — 경고: %s", alert_level.value)
        except Exception as e:
            logger.error("[경량 업데이트] 실패: %s", e)

    async def run_realtime_monitor(self) -> None:
        """장중 5분 간격 감시 — 손절 체크"""
        try:
            stop_orders = self.risk_service.check_stop_loss({})
            for order in stop_orders:
                result = await self.risk_service.process_order(order)
                from app.models.contracts import ApprovedOrder
                if isinstance(result, ApprovedOrder):
                    exec_result = await self.execution_service.execute(result)
                    await self.monitoring_service.on_trade_executed(exec_result)
                    await notifier.stop_loss_triggered(
                        order.ticker, 0.0, order.reason
                    )
        except Exception as e:
            logger.error("[실시간 감시] 실패: %s", e)

    async def run_closing_review(self) -> None:
        """16:30 장마감 리뷰 — 일일 성과 + 리포트"""
        logger.info("[장마감 리뷰] 시작 — %s", datetime.now())
        try:
            await self.monitoring_service.send_daily_report()
            logger.info("[장마감 리뷰] 완료")
        except Exception as e:
            logger.error("[장마감 리뷰] 실패: %s", e)

    async def shutdown(self) -> None:
        """종료 정리"""
        logger.info("시스템 종료 정리 중...")
        try:
            from app.utils.db import close_engine
            from app.utils.redis_client import close_redis
            await close_redis()
            await close_engine()
        except Exception as e:
            logger.error("종료 정리 실패: %s", e)


async def main() -> None:
    """엔트리포인트"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/trading.log", encoding="utf-8"),
        ],
    )

    logger.info("=== 트레이딩 시스템 시작 [%s 모드] ===", settings.mode)

    orchestrator = TradingOrchestrator()

    # Daily Drill 초기화
    drill = None
    try:
        from tests.daily_drill.drill_runner import DailyDrill
        drill = DailyDrill(
            orchestrator.risk_service,
            orchestrator.analysis_service,
            orchestrator.data_service,
        )
        logger.info("DailyDrill 초기화 완료")
    except Exception as e:
        logger.warning("DailyDrill 초기화 실패: %s", e)

    # 스케줄러 시작
    from app.scheduler import create_scheduler
    scheduler = create_scheduler(orchestrator, daily_drill=drill)
    scheduler.start()

    try:
        # 즉시 1회 실행 (시작 시 파이프라인)
        await orchestrator.run_daily_pipeline()

        # 스케줄러 무한 루프
        while True:
            await asyncio.sleep(60)
            await redis_client.set_heartbeat()

    except KeyboardInterrupt:
        logger.info("종료 신호 수신")
    finally:
        scheduler.shutdown()
        await orchestrator.shutdown()
        logger.info("=== 트레이딩 시스템 종료 ===")


if __name__ == "__main__":
    asyncio.run(main())
