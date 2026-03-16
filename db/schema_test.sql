-- ═══════════════════════════════════════════════════════════════
-- 테스트 시스템 DB 테이블
-- schema.sql 실행 후 추가 실행
-- ═══════════════════════════════════════════════════════════════

-- 아침 훈련 결과
CREATE TABLE IF NOT EXISTS drill_results (
    drill_id        SERIAL PRIMARY KEY,
    date            DATE NOT NULL,
    test_type       VARCHAR(30),
    scenario_name   VARCHAR(100),
    passed          BOOLEAN,
    error_type      VARCHAR(20),
    target_check    VARCHAR(30),
    expected_decision VARCHAR(20),
    actual_decision VARCHAR(20),
    llm_reasoning   TEXT,
    severity        VARCHAR(10),
    timestamp       TIMESTAMPTZ DEFAULT NOW()
);

-- 주간 성적표
CREATE TABLE IF NOT EXISTS weekly_scorecards (
    week_start      DATE PRIMARY KEY,
    total_tests     INT,
    pass_rate       NUMERIC(4, 3),
    false_negatives INT,
    false_positives INT,
    readiness_score INT,
    report_json     JSONB
);

-- 위기 리플레이 결과
CREATE TABLE IF NOT EXISTS crisis_replay_results (
    replay_id              SERIAL PRIMARY KEY,
    date                   DATE,
    crisis_name            VARCHAR(50),
    all_passed             BOOLEAN,
    regime_match           BOOLEAN,
    alert_match            BOOLEAN,
    buy_blocked_correctly  BOOLEAN,
    details                JSONB,
    timestamp              TIMESTAMPTZ DEFAULT NOW()
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_drill_date
    ON drill_results (date DESC);

CREATE INDEX IF NOT EXISTS idx_drill_failed
    ON drill_results (passed) WHERE NOT passed;

CREATE INDEX IF NOT EXISTS idx_crisis_replay_date
    ON crisis_replay_results (date DESC);
