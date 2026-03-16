-- ═══════════════════════════════════════════════════════════════
-- 트레이딩 시스템 전체 DB 스키마
-- PostgreSQL 16 + TimescaleDB
-- ═══════════════════════════════════════════════════════════════

-- TimescaleDB 확장 활성화
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ═══ Layer 1: 시장 데이터 ═══

-- 1. 종목 마스터
CREATE TABLE IF NOT EXISTS symbols (
    symbol_id   SERIAL PRIMARY KEY,
    ticker      VARCHAR(20) NOT NULL UNIQUE,
    name        VARCHAR(100) NOT NULL,
    market      VARCHAR(10) NOT NULL CHECK (market IN ('KOSPI', 'KOSDAQ', 'US', 'ETF')),
    asset_type  VARCHAR(20) NOT NULL CHECK (asset_type IN (
        'stock', 'etf_equity', 'etf_bond', 'etf_gold', 'etf_commodity'
    )),
    sector      VARCHAR(50),
    kis_code    VARCHAR(10),           -- KIS API 종목코드 (6자리)
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. 일봉 데이터
CREATE TABLE IF NOT EXISTS daily_ohlcv (
    symbol_id   INT NOT NULL REFERENCES symbols(symbol_id),
    date        DATE NOT NULL,
    open        NUMERIC(12, 2) NOT NULL,
    high        NUMERIC(12, 2) NOT NULL,
    low         NUMERIC(12, 2) NOT NULL,
    close       NUMERIC(12, 2) NOT NULL,
    adj_close   NUMERIC(12, 2),
    volume      BIGINT NOT NULL DEFAULT 0,
    turnover    BIGINT,
    PRIMARY KEY (symbol_id, date)
);

-- 3. 기술적 지표
CREATE TABLE IF NOT EXISTS daily_indicators (
    symbol_id    INT NOT NULL REFERENCES symbols(symbol_id),
    date         DATE NOT NULL,
    ma5          NUMERIC(12, 2),
    ma20         NUMERIC(12, 2),
    ma60         NUMERIC(12, 2),
    ma120        NUMERIC(12, 2),
    rsi14        NUMERIC(8, 4),
    macd         NUMERIC(12, 4),
    macd_signal  NUMERIC(12, 4),
    bb_upper     NUMERIC(12, 2),
    bb_lower     NUMERIC(12, 2),
    atr14        NUMERIC(12, 4),
    adx14        NUMERIC(8, 4),
    obv          BIGINT,
    PRIMARY KEY (symbol_id, date)
);

-- 4. 분봉 데이터 (TimescaleDB 하이퍼테이블)
CREATE TABLE IF NOT EXISTS minute_ohlcv (
    symbol_id   INT NOT NULL,
    timestamp   TIMESTAMPTZ NOT NULL,
    open        NUMERIC(12, 2) NOT NULL,
    high        NUMERIC(12, 2) NOT NULL,
    low         NUMERIC(12, 2) NOT NULL,
    close       NUMERIC(12, 2) NOT NULL,
    volume      BIGINT NOT NULL DEFAULT 0
);

SELECT create_hypertable('minute_ohlcv', 'timestamp', if_not_exists => TRUE);

-- 90일 보존 정책
SELECT add_retention_policy('minute_ohlcv', INTERVAL '90 days', if_not_exists => TRUE);

-- 7일 압축 정책
ALTER TABLE minute_ohlcv SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);
SELECT add_compression_policy('minute_ohlcv', INTERVAL '7 days', if_not_exists => TRUE);

-- 5. ETF 자금흐름
CREATE TABLE IF NOT EXISTS etf_flows (
    symbol_id        INT NOT NULL REFERENCES symbols(symbol_id),
    date             DATE NOT NULL,
    creation_units   NUMERIC(12, 2),
    redemption_units NUMERIC(12, 2),
    net_flow         NUMERIC(14, 2),
    nav              NUMERIC(12, 2),
    premium_discount NUMERIC(8, 4),
    PRIMARY KEY (symbol_id, date)
);

-- 6. 수급 데이터
CREATE TABLE IF NOT EXISTS supply_demand (
    symbol_id       INT NOT NULL REFERENCES symbols(symbol_id),
    date            DATE NOT NULL,
    foreign_net     BIGINT,          -- 외국인 순매매 금액
    institution_net BIGINT,          -- 기관 순매매
    individual_net  BIGINT,          -- 개인 순매매
    short_volume    BIGINT,
    short_balance   BIGINT,
    credit_balance  BIGINT,
    PRIMARY KEY (symbol_id, date)
);

-- 7. 글로벌 지표
CREATE TABLE IF NOT EXISTS global_indicators (
    date            DATE NOT NULL,
    indicator_name  VARCHAR(50) NOT NULL,
    value           NUMERIC(12, 4) NOT NULL,
    PRIMARY KEY (date, indicator_name)
);

-- ═══ Layer 1: 뉴스 데이터 ═══

-- 8. 뉴스 기사
CREATE TABLE IF NOT EXISTS news_articles (
    article_id       SERIAL PRIMARY KEY,
    source           VARCHAR(50) NOT NULL,
    title            VARCHAR(500) NOT NULL,
    summary          TEXT,
    url              VARCHAR(1000),
    published_at     TIMESTAMPTZ NOT NULL,
    collected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    language         VARCHAR(5) DEFAULT 'ko',
    importance_score NUMERIC(4, 2),
    categories       TEXT[],
    sentiment_score  NUMERIC(4, 2),
    is_processed     BOOLEAN NOT NULL DEFAULT FALSE
);

-- 9. 뉴스 빈도 일별 집계
CREATE TABLE IF NOT EXISTS news_frequency_daily (
    date           DATE NOT NULL,
    category       VARCHAR(50) NOT NULL,
    article_count  INT NOT NULL DEFAULT 0,
    avg_sentiment  NUMERIC(4, 2),
    buzz_score     NUMERIC(8, 4),
    tone_shift     NUMERIC(8, 4),
    PRIMARY KEY (date, category)
);

-- ═══ Layer 1: 재무/매크로/대안 ═══

-- 10. 재무제표
CREATE TABLE IF NOT EXISTS financial_statements (
    company_id       INT NOT NULL,
    fiscal_year      INT NOT NULL,
    fiscal_quarter   INT NOT NULL CHECK (fiscal_quarter BETWEEN 1 AND 4),
    revenue          BIGINT,
    operating_income BIGINT,
    net_income       BIGINT,
    total_assets     BIGINT,
    total_liabilities BIGINT,
    total_equity     BIGINT,
    roe              NUMERIC(8, 4),
    roa              NUMERIC(8, 4),
    debt_ratio       NUMERIC(8, 4),
    operating_cf     BIGINT,
    investing_cf     BIGINT,
    financing_cf     BIGINT,
    fcf              BIGINT,
    quality_score    INT CHECK (quality_score BETWEEN 0 AND 100),
    collected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (company_id, fiscal_year, fiscal_quarter)
);

-- 11. 매크로 지표
CREATE TABLE IF NOT EXISTS macro_indicators (
    date            DATE NOT NULL,
    indicator_code  VARCHAR(30) NOT NULL,
    value           NUMERIC(12, 4),
    previous_value  NUMERIC(12, 4),
    consensus       NUMERIC(12, 4),
    surprise        NUMERIC(8, 4),
    mom_change      NUMERIC(8, 4),
    yoy_change      NUMERIC(8, 4),
    percentile_10y  NUMERIC(5, 2),
    PRIMARY KEY (date, indicator_code)
);

-- 12. 경제 이벤트
CREATE TABLE IF NOT EXISTS economic_events (
    event_id          SERIAL PRIMARY KEY,
    event_date        DATE NOT NULL,
    event_time        TIME,
    event_name        VARCHAR(200) NOT NULL,
    importance        VARCHAR(10) NOT NULL CHECK (importance IN ('critical', 'high', 'medium')),
    actual_value      VARCHAR(50),
    consensus_value   VARCHAR(50),
    previous_value    VARCHAR(50),
    impact_assessment TEXT
);

-- 13. 내부자 거래
CREATE TABLE IF NOT EXISTS insider_trades (
    trade_id     SERIAL PRIMARY KEY,
    company_id   INT NOT NULL,
    insider_name VARCHAR(100),
    position     VARCHAR(100),
    trade_type   VARCHAR(20) NOT NULL,
    shares       BIGINT NOT NULL,
    price        NUMERIC(12, 2),
    total_value  BIGINT,
    trade_date   DATE NOT NULL,
    report_date  DATE
);

-- 14. 소셜 감성
CREATE TABLE IF NOT EXISTS social_sentiment (
    date           DATE NOT NULL,
    source         VARCHAR(30) NOT NULL,
    ticker         VARCHAR(20) NOT NULL,
    mention_count  INT NOT NULL DEFAULT 0,
    sentiment_avg  NUMERIC(4, 2),
    mention_ratio  NUMERIC(8, 4),
    PRIMARY KEY (date, source, ticker)
);

-- ═══ Layer 2: LLM ═══

-- 15. LLM 호출 로그
CREATE TABLE IF NOT EXISTS llm_call_log (
    call_id            SERIAL PRIMARY KEY,
    timestamp          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    task_type          VARCHAR(50) NOT NULL,
    model_used         VARCHAR(50) NOT NULL,
    prompt_hash        VARCHAR(64),
    input_tokens       INT,
    output_tokens      INT,
    cost_usd           NUMERIC(10, 6),
    input_summary      TEXT,
    raw_output         TEXT,
    parsed_output      JSONB,
    validation_passed  BOOLEAN,
    validation_errors  TEXT[],
    was_used           BOOLEAN DEFAULT TRUE
);

-- ═══ Layer 3: 신호 ═══

-- 16. 신호 점수
CREATE TABLE IF NOT EXISTS signal_scores (
    date                DATE NOT NULL,
    sector              VARCHAR(50) NOT NULL,
    short_term_score    NUMERIC(6, 2),
    long_term_score     NUMERIC(6, 2),
    convergence_type    VARCHAR(30),
    adjusted_confidence NUMERIC(4, 2),
    market_regime       VARCHAR(20),
    recommended_action  VARCHAR(50),
    PRIMARY KEY (date, sector)
);

-- ═══ Layer 4: 포트폴리오 ═══

-- 17. 포트폴리오 목표 배분
CREATE TABLE IF NOT EXISTS portfolio_targets (
    date           DATE PRIMARY KEY,
    regime         VARCHAR(20) NOT NULL,
    kr_equity_pct  NUMERIC(5, 2),
    us_equity_pct  NUMERIC(5, 2),
    kr_bond_pct    NUMERIC(5, 2),
    us_bond_pct    NUMERIC(5, 2),
    gold_pct       NUMERIC(5, 2),
    cash_rp_pct    NUMERIC(5, 2),
    bond_duration  VARCHAR(10)
);

-- 18. 개별 종목 목표
CREATE TABLE IF NOT EXISTS position_targets (
    date          DATE NOT NULL,
    ticker        VARCHAR(20) NOT NULL,
    target_pct    NUMERIC(5, 2),
    target_amount BIGINT,
    signal_type   VARCHAR(30),
    confidence    NUMERIC(4, 2),
    PRIMARY KEY (date, ticker)
);

-- 19. 리밸런싱 로그
CREATE TABLE IF NOT EXISTS rebalance_log (
    rebalance_id     SERIAL PRIMARY KEY,
    date             DATE NOT NULL,
    trigger_reason   VARCHAR(50) NOT NULL,
    trades_executed  INT NOT NULL DEFAULT 0,
    total_turnover   NUMERIC(8, 4),
    estimated_cost   NUMERIC(12, 2)
);

-- ═══ Layer 5: 리스크 ═══

-- 20. 경고 레벨 이력
CREATE TABLE IF NOT EXISTS alert_level_history (
    timestamp        TIMESTAMPTZ PRIMARY KEY,
    alert_level      VARCHAR(20) NOT NULL,
    triggers         TEXT[],
    actions_taken    TEXT[],
    portfolio_value  BIGINT,
    drawdown         NUMERIC(8, 4)
);

-- 21. 손절 이벤트
CREATE TABLE IF NOT EXISTS stop_loss_events (
    event_id    SERIAL PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type  VARCHAR(30) NOT NULL,
    ticker      VARCHAR(20),
    loss_pct    NUMERIC(8, 4),
    amount_sold BIGINT,
    reason      TEXT
);

-- 22. 냉각 기간
CREATE TABLE IF NOT EXISTS cooling_periods (
    period_id        SERIAL PRIMARY KEY,
    start_date       DATE NOT NULL,
    end_date         DATE NOT NULL,
    trigger_reason   VARCHAR(100),
    monthly_loss_pct NUMERIC(8, 4)
);

-- 23. 재진입 로그
CREATE TABLE IF NOT EXISTS reentry_log (
    entry_id       SERIAL PRIMARY KEY,
    date           DATE NOT NULL,
    conditions_met JSONB,
    action_taken   VARCHAR(50),
    tranche_number INT,
    amount         BIGINT,
    price          NUMERIC(12, 2)
);

-- ═══ Layer 6: 주문 ═══

-- 24. 주문
CREATE TABLE IF NOT EXISTS orders (
    order_id        VARCHAR(30) PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    broker          VARCHAR(20) NOT NULL,
    market          VARCHAR(10) NOT NULL,
    ticker          VARCHAR(20) NOT NULL,
    side            VARCHAR(10) NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type      VARCHAR(10) NOT NULL DEFAULT 'limit',
    quantity        INT NOT NULL,
    price           NUMERIC(12, 2),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    filled_quantity INT DEFAULT 0,
    filled_price    NUMERIC(12, 2),
    slippage_pct    NUMERIC(8, 4),
    commission      NUMERIC(10, 2) DEFAULT 0,
    tax             NUMERIC(10, 2) DEFAULT 0,
    trigger_source  VARCHAR(30),
    error_code      VARCHAR(20),
    error_message   TEXT
);

-- 25. 환전 거래
CREATE TABLE IF NOT EXISTS fx_transactions (
    tx_id          SERIAL PRIMARY KEY,
    timestamp      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    from_currency  VARCHAR(5) NOT NULL,
    to_currency    VARCHAR(5) NOT NULL,
    amount         NUMERIC(14, 2) NOT NULL,
    exchange_rate  NUMERIC(10, 4) NOT NULL,
    fee            NUMERIC(10, 2) DEFAULT 0
);

-- ═══ Layer 7: 모니터링 ═══

-- 26. 일일 성과
CREATE TABLE IF NOT EXISTS daily_performance (
    date              DATE PRIMARY KEY,
    portfolio_value   BIGINT NOT NULL,
    daily_return      NUMERIC(8, 4),
    cumulative_return NUMERIC(8, 4),
    drawdown          NUMERIC(8, 4),
    max_drawdown      NUMERIC(8, 4),
    sharpe_ratio      NUMERIC(8, 4),
    volatility        NUMERIC(8, 4),
    equity_pct        NUMERIC(5, 2),
    bond_pct          NUMERIC(5, 2),
    gold_pct          NUMERIC(5, 2),
    cash_pct          NUMERIC(5, 2),
    trade_count       INT DEFAULT 0,
    total_commission  NUMERIC(10, 2) DEFAULT 0,
    regime            VARCHAR(20),
    alert_level       VARCHAR(20)
);

-- 27. 거래 이력
CREATE TABLE IF NOT EXISTS trade_history (
    trade_id     SERIAL PRIMARY KEY,
    date         DATE NOT NULL,
    ticker       VARCHAR(20) NOT NULL,
    side         VARCHAR(10) NOT NULL,
    quantity     INT NOT NULL,
    price        NUMERIC(12, 2) NOT NULL,
    amount       BIGINT NOT NULL,
    commission   NUMERIC(10, 2) DEFAULT 0,
    tax          NUMERIC(10, 2) DEFAULT 0,
    trigger      VARCHAR(30),
    pnl          BIGINT,
    pnl_pct      NUMERIC(8, 4),
    holding_days INT
);


-- ═══════════════════════════════════════
-- 인덱스
-- ═══════════════════════════════════════

-- 뉴스
CREATE INDEX IF NOT EXISTS idx_news_published_at
    ON news_articles (published_at DESC);

CREATE INDEX IF NOT EXISTS idx_news_categories
    ON news_articles USING GIN (categories);

CREATE INDEX IF NOT EXISTS idx_news_unprocessed
    ON news_articles (article_id) WHERE NOT is_processed;

-- 일봉
CREATE INDEX IF NOT EXISTS idx_daily_ohlcv_date
    ON daily_ohlcv (date DESC);

-- 주문
CREATE INDEX IF NOT EXISTS idx_orders_timestamp
    ON orders (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_orders_status
    ON orders (status);

-- LLM 호출 로그
CREATE INDEX IF NOT EXISTS idx_llm_call_log_task
    ON llm_call_log (task_type, timestamp DESC);

-- 거래 이력
CREATE INDEX IF NOT EXISTS idx_trade_history_date
    ON trade_history (date DESC);

CREATE INDEX IF NOT EXISTS idx_trade_history_ticker
    ON trade_history (ticker, date DESC);
