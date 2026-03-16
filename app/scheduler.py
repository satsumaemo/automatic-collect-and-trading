"""
APScheduler — 전체 배치 작업 등록.

KST 기준 스케줄:
  07:00 — 데이터 배치 수집
  07:30 — 메인 파이프라인 (LLM → 신호 → 주문)
  08:30 — 아침 훈련 (장 시작 전)
  12:30 — 미드데이 업데이트
  16:30 — 일일 리포트
  매시간 — 글로벌 지표 수집
"""

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)


def create_scheduler(
    orchestrator: "TradingOrchestrator",  # noqa: F821
    daily_drill: Optional[object] = None,
) -> AsyncIOScheduler:
    """스케줄러 생성 및 작업 등록"""
    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    # 매일 07:30 — 메인 파이프라인
    scheduler.add_job(
        orchestrator.run_daily_pipeline,
        "cron", hour=7, minute=30,
        id="daily_pipeline",
        misfire_grace_time=300,
    )

    # 매일 08:30 — 아침 훈련
    if daily_drill and hasattr(daily_drill, "run"):
        scheduler.add_job(
            daily_drill.run,
            "cron", hour=8, minute=30,
            id="daily_drill",
            misfire_grace_time=300,
        )

    # 매일 12:30 — 미드데이 업데이트
    scheduler.add_job(
        orchestrator.run_midday_update,
        "cron", hour=12, minute=30,
        id="midday_update",
        misfire_grace_time=300,
    )

    # 매일 16:30 — 장마감 리뷰 + 일일 리포트
    scheduler.add_job(
        orchestrator.run_closing_review,
        "cron", hour=16, minute=30,
        id="closing_review",
        misfire_grace_time=300,
    )

    # 매시간 — 글로벌 지표 수집
    scheduler.add_job(
        orchestrator.data_service.collect_hourly,
        "interval", hours=1,
        id="hourly_collect",
        misfire_grace_time=120,
    )

    logger.info("스케줄러 작업 %d개 등록 완료", len(scheduler.get_jobs()))
    return scheduler
