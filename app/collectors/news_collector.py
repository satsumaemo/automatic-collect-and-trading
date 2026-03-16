"""
뉴스 수집기.
RSS 피드로 국내외 뉴스를 수집하고 카테고리 분류 + 중요도 점수를 계산합니다.

feedparser는 동기 라이브러리 → asyncio.to_thread()로 감싸서 사용.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta, date
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text

from app.utils.db import get_session

logger = logging.getLogger(__name__)

# ── RSS 피드 소스 ──
RSS_FEEDS: Dict[str, dict] = {
    "hankyung": {
        "url": "https://www.hankyung.com/feed/economy",
        "source_name": "hankyung",
        "language": "ko",
    },
    "mk": {
        "url": "https://www.mk.co.kr/rss/30100041/",
        "source_name": "mk",
        "language": "ko",
    },
    "yonhap_economy": {
        "url": "https://www.yna.co.kr/rss/economy.xml",
        "source_name": "yonhap",
        "language": "ko",
    },
    "reuters_business": {
        "url": "https://feeds.reuters.com/reuters/businessNews",
        "source_name": "reuters",
        "language": "en",
    },
    "google_news_business": {
        "url": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtdHZHZ0pMVWlnQVAB",
        "source_name": "google_news",
        "language": "ko",
    },
}

# ── 카테고리 키워드 사전 ──
NEWS_CATEGORIES: Dict[str, dict] = {
    "macro_monetary": {
        "keywords": ["금리", "기준금리", "FOMC", "금통위", "양적완화", "양적긴축", "통화정책", "인플레이션", "CPI", "물가"],
        "weight": 1.0,
    },
    "macro_fiscal": {
        "keywords": ["재정", "국채", "예산", "세금", "정부지출", "IRA", "CHIPS"],
        "weight": 0.8,
    },
    "geopolitical": {
        "keywords": ["전쟁", "제재", "관세", "무역", "미중", "중동", "NATO", "선거", "탄핵"],
        "weight": 0.9,
    },
    "sector_tech": {
        "keywords": ["AI", "반도체", "GPU", "데이터센터", "클라우드", "엔비디아", "TSMC", "삼성전자", "SK하이닉스"],
        "weight": 0.8,
    },
    "sector_energy": {
        "keywords": ["유가", "원유", "OPEC", "천연가스", "신재생", "태양광", "전기차", "배터리", "2차전지"],
        "weight": 0.7,
    },
    "sector_finance": {
        "keywords": ["은행", "보험", "증권", "금융", "PER", "PBR", "밸류업", "배당", "자사주"],
        "weight": 0.7,
    },
    "market_sentiment": {
        "keywords": ["급등", "급락", "폭락", "랠리", "패닉", "버블", "과매수", "과매도", "서킷브레이커"],
        "weight": 0.9,
    },
    "earnings": {
        "keywords": ["실적", "어닝", "매출", "영업이익", "순이익", "컨센서스", "서프라이즈"],
        "weight": 0.8,
    },
}

# ── 소스별 가중치 ──
SOURCE_WEIGHTS: Dict[str, float] = {
    "reuters": 1.2,
    "bloomberg": 1.2,
    "hankyung": 1.0,
    "mk": 1.0,
    "yonhap": 1.0,
    "google_news": 0.8,
}


def classify_article(title: str, summary: str) -> List[str]:
    """키워드 기반 카테고리 분류"""
    text_combined = (title or "") + " " + (summary or "")
    matched = []
    for cat_name, cat_cfg in NEWS_CATEGORIES.items():
        hits = sum(1 for kw in cat_cfg["keywords"] if kw in text_combined)
        if hits > 0:
            matched.append(cat_name)
    return matched if matched else ["general"]


def score_article(
    title: str,
    summary: str,
    source: str,
    published_at: Optional[datetime],
) -> Tuple[float, List[str]]:
    """중요도 점수 계산 (0~1)"""
    text_combined = (title or "") + " " + (summary or "")
    score = 0.0
    matched: List[str] = []

    for cat_name, cat_cfg in NEWS_CATEGORIES.items():
        hits = sum(1 for kw in cat_cfg["keywords"] if kw in text_combined)
        if hits > 0:
            matched.append(cat_name)
            score += cat_cfg["weight"] * min(hits / 3.0, 1.0)

    # 소스 가중치
    score *= SOURCE_WEIGHTS.get(source, 0.8)

    # 시간 감쇠 (24시간 반감기)
    if published_at:
        now_utc = datetime.now(timezone.utc)
        pub_utc = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
        hours_old = max((now_utc - pub_utc).total_seconds() / 3600, 0)
        score *= 0.5 ** (hours_old / 24)

    return min(score, 1.0), matched


def _parse_feed_sync(url: str) -> list:
    """feedparser로 RSS 파싱 (동기)"""
    import feedparser
    feed = feedparser.parse(url)
    return feed.entries


class NewsCollector:
    """RSS 기반 뉴스 수집"""

    def __init__(self) -> None:
        logger.info("NewsCollector 초기화 (%d개 피드)", len(RSS_FEEDS))

    async def collect_all_feeds(self) -> None:
        """모든 RSS 피드 수집 → DB 저장"""
        total_new = 0
        for feed_key, feed_cfg in RSS_FEEDS.items():
            try:
                count = await self._parse_and_save(feed_cfg)
                total_new += count
            except Exception as e:
                logger.error("RSS 수집 실패 [%s]: %s", feed_key, e)

        logger.info("뉴스 수집 완료: %d건 신규 저장", total_new)

    async def _parse_and_save(self, feed_cfg: dict) -> int:
        """단일 RSS 피드 파싱 → DB 저장"""
        entries = await asyncio.to_thread(_parse_feed_sync, feed_cfg["url"])
        if not entries:
            return 0

        source = feed_cfg["source_name"]
        language = feed_cfg["language"]
        new_count = 0

        async with get_session() as session:
            for entry in entries:
                title = (entry.get("title") or "")[:500]
                summary = entry.get("summary", "") or entry.get("description", "")
                link = (entry.get("link") or "")[:1000]

                if not title:
                    continue

                # 발행 시각 파싱
                published_at = self._parse_datetime(entry)

                # 중복 체크 (URL 기준)
                if link:
                    dup = await session.execute(
                        text("SELECT 1 FROM news_articles WHERE url = :url LIMIT 1"),
                        {"url": link},
                    )
                    if dup.fetchone():
                        continue

                # 카테고리 분류 + 중요도
                categories = classify_article(title, summary)
                importance, _ = score_article(title, summary, source, published_at)

                await session.execute(
                    text("""
                        INSERT INTO news_articles
                            (source, title, summary, url, published_at,
                             language, importance_score, categories, is_processed)
                        VALUES
                            (:src, :title, :summary, :url, :pub,
                             :lang, :imp, :cats, FALSE)
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "src": source,
                        "title": title,
                        "summary": summary[:2000] if summary else None,
                        "url": link or None,
                        "pub": published_at or datetime.now(timezone.utc),
                        "lang": language,
                        "imp": importance,
                        "cats": categories,
                    },
                )
                new_count += 1

        return new_count

    @staticmethod
    def _parse_datetime(entry: dict) -> Optional[datetime]:
        """feedparser 엔트리에서 datetime 추출"""
        import time as _time
        from email.utils import parsedate_to_datetime

        # published_parsed가 있으면 사용
        pp = entry.get("published_parsed")
        if pp:
            try:
                return datetime(*pp[:6], tzinfo=timezone.utc)
            except Exception:
                pass

        # published 문자열 파싱
        pub_str = entry.get("published") or entry.get("updated")
        if pub_str:
            try:
                return parsedate_to_datetime(pub_str)
            except Exception:
                pass

        return None

    async def update_daily_frequency(self) -> None:
        """news_frequency_daily 테이블 일별 집계 업데이트"""
        today = date.today()

        async with get_session() as session:
            # 오늘 날짜 기사에서 카테고리별 집계
            result = await session.execute(
                text("""
                    SELECT unnest(categories) AS category,
                           COUNT(*) AS cnt,
                           AVG(COALESCE(sentiment_score, 0)) AS avg_sent
                    FROM news_articles
                    WHERE published_at::date = :today
                    GROUP BY category
                """),
                {"today": today},
            )
            rows = result.fetchall()

            for row in rows:
                category = row[0]
                count = row[1]
                avg_sentiment = float(row[2]) if row[2] else 0

                # 최근 30일 평균 기사 수
                avg_result = await session.execute(
                    text("""
                        SELECT AVG(article_count)
                        FROM news_frequency_daily
                        WHERE category = :cat
                          AND date >= :cutoff
                          AND date < :today
                    """),
                    {
                        "cat": category,
                        "cutoff": today - timedelta(days=30),
                        "today": today,
                    },
                )
                avg_row = avg_result.fetchone()
                avg_30d = float(avg_row[0]) if avg_row and avg_row[0] else max(count, 1)

                buzz_score = count / avg_30d if avg_30d > 0 else 1.0

                # 전일 감성 대비 변화
                prev_result = await session.execute(
                    text("""
                        SELECT avg_sentiment FROM news_frequency_daily
                        WHERE category = :cat AND date = :yesterday
                    """),
                    {"cat": category, "yesterday": today - timedelta(days=1)},
                )
                prev_row = prev_result.fetchone()
                prev_sentiment = float(prev_row[0]) if prev_row and prev_row[0] else 0
                tone_shift = avg_sentiment - prev_sentiment

                await session.execute(
                    text("""
                        INSERT INTO news_frequency_daily
                            (date, category, article_count, avg_sentiment, buzz_score, tone_shift)
                        VALUES
                            (:dt, :cat, :cnt, :avg_s, :buzz, :shift)
                        ON CONFLICT (date, category) DO UPDATE SET
                            article_count = EXCLUDED.article_count,
                            avg_sentiment = EXCLUDED.avg_sentiment,
                            buzz_score = EXCLUDED.buzz_score,
                            tone_shift = EXCLUDED.tone_shift
                    """),
                    {
                        "dt": today,
                        "cat": category,
                        "cnt": count,
                        "avg_s": avg_sentiment,
                        "buzz": buzz_score,
                        "shift": tone_shift,
                    },
                )

        logger.info("뉴스 빈도 집계 업데이트 완료")
