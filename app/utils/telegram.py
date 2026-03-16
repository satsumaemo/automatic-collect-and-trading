"""
텔레그램 봇 알림.
쿨다운 관리로 스팸 방지, 전송 실패 시 3회 재시도.
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from telegram import Bot
from telegram.error import TelegramError

from app.config import settings

logger = logging.getLogger(__name__)


# ── 쿨다운 설정 (초) ──
COOLDOWN_MAP: Dict[str, int] = {
    "daily_summary": 86400,
    "trade_executed": 60,
    "alert_level_change": 60,
    "stop_loss_triggered": 0,       # 즉시 전송
    "system_error": 300,
    "llm_analysis_complete": 300,
}

MAX_RETRIES = 3


class TelegramNotifier:
    """텔레그램 알림 발송기"""

    def __init__(self) -> None:
        self._bot: Optional[Bot] = None
        self._chat_id: str = settings.telegram.chat_id
        self._last_sent: Dict[str, float] = {}  # 알림 유형별 마지막 전송 시각

    def _get_bot(self) -> Bot:
        if self._bot is None:
            if not settings.telegram.bot_token:
                raise ValueError("TELEGRAM_BOT_TOKEN이 설정되지 않았습니다")
            self._bot = Bot(token=settings.telegram.bot_token)
        return self._bot

    def _check_cooldown(self, alert_type: str) -> bool:
        """쿨다운 확인. True면 전송 가능."""
        cooldown = COOLDOWN_MAP.get(alert_type, 60)
        if cooldown == 0:
            return True
        last = self._last_sent.get(alert_type, 0.0)
        return (time.time() - last) >= cooldown

    async def send_message(self, text: str, alert_type: str = "system_error") -> bool:
        """
        메시지 전송. 쿨다운 중이면 스킵.
        실패 시 3회 재시도 후 로깅만 수행.
        """
        if not self._check_cooldown(alert_type):
            logger.debug("쿨다운 중 — %s 전송 스킵", alert_type)
            return False

        bot = self._get_bot()
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await bot.send_message(
                    chat_id=self._chat_id,
                    text=text,
                    parse_mode="HTML",
                )
                self._last_sent[alert_type] = time.time()
                return True
            except TelegramError as e:
                logger.warning("텔레그램 전송 실패 (%d/%d): %s", attempt, MAX_RETRIES, e)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(1.0)

        logger.error("텔레그램 전송 최종 실패 — %s", alert_type)
        return False

    # ── 알림 템플릿 ──

    async def daily_summary(self, report: dict) -> bool:
        """일일 리포트 전송"""
        text = (
            "<b>📊 일일 리포트</b>\n"
            f"포트폴리오: {report.get('portfolio_value', 0):,}원\n"
            f"일일수익률: {report.get('daily_return', 0):.2%}\n"
            f"누적수익률: {report.get('cumulative_return', 0):.2%}\n"
            f"MDD: {report.get('max_drawdown', 0):.2%}\n"
            f"레짐: {report.get('regime', '-')}\n"
            f"경고: {report.get('alert_level', '-')}"
        )
        return await self.send_message(text, "daily_summary")

    async def trade_executed(self, trade: dict) -> bool:
        """거래 체결 알림"""
        side_emoji = "🔵" if trade.get("side") == "buy" else "🔴"
        text = (
            f"{side_emoji} <b>거래 체결</b>\n"
            f"{trade.get('ticker', '')} {trade.get('side', '').upper()}\n"
            f"수량: {trade.get('quantity', 0):,}\n"
            f"가격: {trade.get('price', 0):,.0f}원\n"
            f"사유: {trade.get('trigger', '-')}"
        )
        return await self.send_message(text, "trade_executed")

    async def alert_level_change(self, old_level: str, new_level: str) -> bool:
        """경고 레벨 변경 알림"""
        text = (
            f"⚠️ <b>경고 레벨 변경</b>\n"
            f"{old_level.upper()} → {new_level.upper()}"
        )
        return await self.send_message(text, "alert_level_change")

    async def stop_loss_triggered(self, ticker: str, loss_pct: float, reason: str) -> bool:
        """손절 발동 알림"""
        text = (
            f"🛑 <b>손절 발동</b>\n"
            f"종목: {ticker}\n"
            f"손실: {loss_pct:.2%}\n"
            f"사유: {reason}"
        )
        return await self.send_message(text, "stop_loss_triggered")

    async def system_error(self, error: str) -> bool:
        """시스템 오류 알림"""
        text = f"🚨 <b>시스템 오류</b>\n{error[:500]}"
        return await self.send_message(text, "system_error")

    async def llm_analysis_complete(self, analysis_type: str, summary: str) -> bool:
        """LLM 분석 완료 알림"""
        text = (
            f"🤖 <b>LLM 분석 완료</b>\n"
            f"유형: {analysis_type}\n"
            f"{summary[:300]}"
        )
        return await self.send_message(text, "llm_analysis_complete")


# 전역 알림 인스턴스
notifier = TelegramNotifier()
