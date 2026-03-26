"""DB 읽기전용 접근 — 메인 시스템 DB에서 SELECT만"""

import asyncio
import json
import logging
import psycopg2
import psycopg2.extras
import redis

from app.config import settings

logger = logging.getLogger(__name__)


class DBReader:
    def __init__(self):
        pg = settings.postgres
        self.dsn = f"postgresql://{pg.user}:{pg.password}@{pg.host}:{pg.port}/{pg.db}"

    def _get_conn(self):
        conn = psycopg2.connect(self.dsn)
        conn.set_client_encoding("UTF8")
        return conn

    # ── 시장 레짐 ──

    def get_latest_regime(self) -> dict:
        sql = """
            SELECT parsed_output
            FROM llm_call_log
            WHERE task_type = 'regime' AND validation_passed = TRUE
            ORDER BY timestamp DESC
            LIMIT 1
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql)
                row = cur.fetchone()
        if not row or not row["parsed_output"]:
            return {"regime": "unknown", "confidence": 0}
        po = row["parsed_output"]
        if isinstance(po, str):
            po = json.loads(po)
        return {
            "regime": po.get("regime", "unknown"),
            "confidence": po.get("regime_confidence", 0),
            "reasoning": po.get("regime_reasoning", ""),
            "asset_allocation_suggestion": po.get("asset_allocation_suggestion", {}),
        }

    # ── 글로벌·매크로 지표 ──

    def get_market_indicators(self) -> list[dict]:
        indicators = []
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT indicator_name AS name, value
                    FROM global_indicators
                    WHERE date = (SELECT MAX(date) FROM global_indicators)
                """)
                indicators.extend([dict(r) for r in cur.fetchall()])

                cur.execute("""
                    SELECT indicator_code AS name, value
                    FROM macro_indicators
                    WHERE date = (SELECT MAX(date) FROM macro_indicators)
                """)
                indicators.extend([dict(r) for r in cur.fetchall()])
        return indicators

    # ── 뉴스 ──

    def get_recent_news(self, days: int = 2, limit: int = 15) -> list[dict]:
        sql = """
            SELECT title, source, summary, sentiment_score, published_at
            FROM news_articles
            WHERE published_at >= NOW() - INTERVAL '%s days'
            ORDER BY importance_score DESC NULLS LAST
            LIMIT %s
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (days, limit))
                return [dict(r) for r in cur.fetchall()]

    def get_news_by_category(self, category: str, days: int = 7, limit: int = 10) -> list[dict]:
        sql = """
            SELECT title, source, summary, sentiment_score, published_at
            FROM news_articles
            WHERE %s = ANY(categories)
              AND published_at >= NOW() - INTERVAL '%s days'
            ORDER BY importance_score DESC NULLS LAST
            LIMIT %s
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (category, days, limit))
                return [dict(r) for r in cur.fetchall()]

    def get_news_buzz(self) -> list[dict]:
        sql = """
            SELECT category, buzz_score, avg_sentiment
            FROM news_frequency_daily
            WHERE date >= CURRENT_DATE - INTERVAL '7 days'
              AND buzz_score > 1.5
            ORDER BY buzz_score DESC
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql)
                return [dict(r) for r in cur.fetchall()]

    # ── LLM 분석 ──

    def get_latest_analyses(self, limit: int = 5) -> list[dict]:
        sql = """
            SELECT task_type, parsed_output, timestamp
            FROM llm_call_log
            WHERE validation_passed = TRUE
            ORDER BY timestamp DESC
            LIMIT %s
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (limit,))
                rows = []
                for r in cur.fetchall():
                    d = dict(r)
                    if isinstance(d.get("parsed_output"), str):
                        d["parsed_output"] = json.loads(d["parsed_output"])
                    rows.append(d)
                return rows

    # ── 포지션 ──

    def get_current_positions(self) -> dict:
        """
        보유 포지션 조회. 3단계 폴백:
          1) KIS API 잔고 조회 (실시간)
          2) Redis portfolio:positions
          3) trade_history 테이블 집계
        Returns: {"positions": [...], "cash": int, "total_eval": int}
        """
        # 방법 1: KIS API
        try:
            from app.brokers.kis_broker import KISBroker
            broker = KISBroker()
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        balance = pool.submit(asyncio.run, broker.get_balance()).result(timeout=15)
                else:
                    balance = loop.run_until_complete(broker.get_balance())
            except RuntimeError:
                balance = asyncio.run(broker.get_balance())

            logger.info("KIS API 잔고 조회 성공: 포지션 %d개", len(balance.get("positions", [])))
            return {
                "positions": balance.get("positions", []),
                "cash": balance.get("cash", 0),
                "total_eval": balance.get("total_eval", 0),
                "eval_pnl": balance.get("eval_pnl", 0),
                "source": "kis_api",
            }
        except Exception as e:
            logger.warning("KIS API 잔고 조회 실패, Redis 폴백: %s", e)

        # 방법 2: Redis
        try:
            rcfg = settings.redis
            r = redis.Redis(
                host=rcfg.host,
                port=rcfg.port,
                db=rcfg.db,
                password=rcfg.password,
                decode_responses=True,
            )
            raw = r.get("portfolio:positions")
            if raw:
                data = json.loads(raw)
                positions = data if isinstance(data, list) else data.get("positions", [])
                cash = data.get("cash", 0) if isinstance(data, dict) else 0
                total_eval = data.get("total_eval", 0) if isinstance(data, dict) else 0
                if positions:
                    logger.info("Redis 포지션 조회 성공: %d개", len(positions))
                    return {
                        "positions": positions,
                        "cash": cash,
                        "total_eval": total_eval,
                        "source": "redis",
                    }
        except Exception as e:
            logger.warning("Redis 포지션 조회 실패, trade_history 폴백: %s", e)

        # 방법 3: trade_history 집계
        try:
            positions = self._estimate_positions_from_trades()
            if positions:
                logger.info("trade_history 포지션 추정: %d개", len(positions))
                return {
                    "positions": positions,
                    "cash": 0,
                    "total_eval": 0,
                    "source": "trade_history",
                }
        except Exception as e:
            logger.warning("trade_history 포지션 추정 실패: %s", e)

        # 모두 실패 — 보유 종목 없음
        return {
            "positions": [],
            "cash": 50_000_000,
            "total_eval": 50_000_000,
            "source": "default",
        }

    def _estimate_positions_from_trades(self) -> list[dict]:
        """trade_history에서 매수/매도 집계로 현재 보유 추정"""
        sql = """
            SELECT ticker,
                   SUM(CASE WHEN side = 'buy' THEN quantity ELSE -quantity END) AS net_qty,
                   SUM(CASE WHEN side = 'buy' THEN quantity * price ELSE 0 END) /
                       NULLIF(SUM(CASE WHEN side = 'buy' THEN quantity ELSE 0 END), 0) AS avg_price
            FROM trade_history
            GROUP BY ticker
            HAVING SUM(CASE WHEN side = 'buy' THEN quantity ELSE -quantity END) > 0
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql)
                rows = cur.fetchall()
        positions = []
        for r in rows:
            positions.append({
                "ticker": r["ticker"],
                "quantity": int(r["net_qty"]),
                "avg_price": float(r["avg_price"] or 0),
                "current_price": 0,
                "pnl_pct": 0,
            })
        return positions

    # ── 거래 이력 ──

    def get_trade_history(self, days: int = 7, limit: int = 20) -> list[dict]:
        sql = """
            SELECT date, ticker, side, quantity, price, amount, pnl, pnl_pct, trigger
            FROM trade_history
            WHERE date >= CURRENT_DATE - INTERVAL '%s days'
            ORDER BY date DESC, trade_id DESC
            LIMIT %s
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (days, limit))
                return [dict(r) for r in cur.fetchall()]

    # ── 일일 성과 ──

    def get_daily_performance(self, days: int = 30) -> list[dict]:
        sql = """
            SELECT date, portfolio_value, daily_return, cumulative_return,
                   drawdown, sharpe_ratio, regime
            FROM daily_performance
            ORDER BY date DESC
            LIMIT %s
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (days,))
                return [dict(r) for r in cur.fetchall()]

    # ── ETF 유니버스 ──

    def get_etf_universe(self) -> list[dict]:
        sql = """
            SELECT s.ticker, s.name, s.asset_type, s.sector,
                   o.close AS latest_close, o.date AS price_date
            FROM symbols s
            LEFT JOIN LATERAL (
                SELECT close, date
                FROM daily_ohlcv
                WHERE symbol_id = s.symbol_id
                ORDER BY date DESC
                LIMIT 1
            ) o ON TRUE
            WHERE s.is_active = TRUE
            ORDER BY s.ticker
        """
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql)
                return [dict(r) for r in cur.fetchall()]
