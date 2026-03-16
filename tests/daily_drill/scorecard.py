"""
누적 성적표 + 실전 전환 준비도 점수.
주간 집계 및 텔레그램 리포트.
"""

import json
import logging
from datetime import date, timedelta
from typing import Dict, Optional

from sqlalchemy import text

from app.utils.db import get_session
from app.utils.telegram import notifier

logger = logging.getLogger(__name__)


class ValidationScorecard:
    """누적 성적표 및 준비도 점수"""

    async def generate_weekly_report(self) -> Dict:
        """최근 7일 drill_results 집계"""
        cutoff = date.today() - timedelta(days=7)

        async with get_session() as session:
            # 전체 통과율
            result = await session.execute(
                text("""
                    SELECT COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE passed) AS pass_count
                    FROM drill_results
                    WHERE date >= :cutoff
                """),
                {"cutoff": cutoff},
            )
            row = result.fetchone()
            total = row[0] if row else 0
            pass_count = row[1] if row else 0
            pass_rate = pass_count / max(total, 1)

            # False Negative 수
            fn_result = await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM drill_results
                    WHERE date >= :cutoff AND error_type = 'FALSE_NEGATIVE'
                """),
                {"cutoff": cutoff},
            )
            false_negatives = fn_result.scalar() or 0

            # False Positive 수
            fp_result = await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM drill_results
                    WHERE date >= :cutoff AND error_type = 'FALSE_POSITIVE'
                """),
                {"cutoff": cutoff},
            )
            false_positives = fp_result.scalar() or 0

            # Kill Switch 정상률
            ks_result = await session.execute(
                text("""
                    SELECT COUNT(*) FILTER (WHERE passed),
                           COUNT(*)
                    FROM drill_results
                    WHERE date >= :cutoff AND test_type = 'kill_switch'
                """),
                {"cutoff": cutoff},
            )
            ks_row = ks_result.fetchone()
            ks_ok = ks_row[0] if ks_row else 0
            ks_total = ks_row[1] if ks_row else 0

        report = {
            "week_start": cutoff.isoformat(),
            "total_tests": total,
            "pass_count": pass_count,
            "pass_rate": round(pass_rate, 3),
            "false_negatives": false_negatives,
            "false_positives": false_positives,
            "kill_switch_ok": ks_ok,
            "kill_switch_total": ks_total,
        }

        report["readiness_score"] = self._calculate_readiness(report)
        return report

    def _calculate_readiness(self, report: Dict) -> int:
        """준비도 점수 계산 (0~100)"""
        score = 0

        # 통과율 × 40점 (85% 이상 만점)
        pr = report.get("pass_rate", 0)
        if pr >= 0.85:
            score += 40
        else:
            score += int(pr / 0.85 * 40)

        # False Negative 0건 = 30점 (1건당 -10점)
        fn = report.get("false_negatives", 0)
        fn_score = max(0, 30 - fn * 10)
        score += fn_score

        # False Positive 10% 이하 = 15점
        total = max(report.get("total_tests", 1), 1)
        fp_rate = report.get("false_positives", 0) / total
        if fp_rate <= 0.10:
            score += 15
        else:
            score += max(0, int(15 * (1 - (fp_rate - 0.10) / 0.20)))

        # Kill Switch 100% 정상 = 15점
        ks_total = max(report.get("kill_switch_total", 1), 1)
        ks_rate = report.get("kill_switch_ok", 0) / ks_total
        score += int(15 * ks_rate)

        return min(score, 100)

    async def calculate_readiness(self) -> int:
        """준비도 점수만 반환"""
        report = await self.generate_weekly_report()
        return report["readiness_score"]

    async def send_weekly_report(self) -> None:
        """주간 성적표 텔레그램 발송 + DB 저장"""
        report = await self.generate_weekly_report()

        # DB 저장
        try:
            async with get_session() as session:
                await session.execute(
                    text("""
                        INSERT INTO weekly_scorecards
                            (week_start, total_tests, pass_rate,
                             false_negatives, false_positives,
                             readiness_score, report_json)
                        VALUES
                            (:ws, :total, :pr, :fn, :fp, :rs, :rj)
                        ON CONFLICT (week_start) DO UPDATE SET
                            total_tests = EXCLUDED.total_tests,
                            pass_rate = EXCLUDED.pass_rate,
                            false_negatives = EXCLUDED.false_negatives,
                            false_positives = EXCLUDED.false_positives,
                            readiness_score = EXCLUDED.readiness_score,
                            report_json = EXCLUDED.report_json
                    """),
                    {
                        "ws": report["week_start"],
                        "total": report["total_tests"],
                        "pr": report["pass_rate"],
                        "fn": report["false_negatives"],
                        "fp": report["false_positives"],
                        "rs": report["readiness_score"],
                        "rj": json.dumps(report, ensure_ascii=False),
                    },
                )
        except Exception as e:
            logger.error("주간 성적표 DB 저장 실패: %s", e)

        # 텔레그램
        msg = (
            f"📊 <b>주간 성적표</b>\n"
            f"기간: {report['week_start']} ~ 오늘\n"
            f"총 테스트: {report['total_tests']}\n"
            f"통과율: {report['pass_rate']:.0%}\n"
            f"False Negative: {report['false_negatives']}건\n"
            f"False Positive: {report['false_positives']}건\n"
            f"Kill Switch: {report['kill_switch_ok']}/{report['kill_switch_total']}\n"
            f"<b>준비도 점수: {report['readiness_score']}/100</b>"
        )
        try:
            await notifier.send_message(msg, "daily_summary")
        except Exception as e:
            logger.error("주간 성적표 알림 실패: %s", e)
