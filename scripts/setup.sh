#!/usr/bin/env bash
# ═══════════════════════════════════════
# 트레이딩 시스템 초기 설정 스크립트
# ═══════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== 트레이딩 시스템 초기 설정 ==="

# 1. .env 파일 생성 (없을 때만)
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "[1/6] .env 파일 생성 완료 — 값을 입력해주세요"
else
    echo "[1/6] .env 파일 이미 존재 — 스킵"
fi

# 2. Docker Compose로 PostgreSQL + Redis + Grafana 시작
echo "[2/6] Docker 컨테이너 시작..."
cd "$PROJECT_DIR"
docker compose up -d
echo "      컨테이너 시작 완료"

# 3. PostgreSQL 준비 대기
echo "[3/6] PostgreSQL 준비 대기..."
for i in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U trading > /dev/null 2>&1; then
        echo "      PostgreSQL 준비 완료"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "      [오류] PostgreSQL 시작 시간 초과"
        exit 1
    fi
    sleep 1
done

# 4. TimescaleDB 확장 활성화 + 스키마 실행
echo "[4/6] TimescaleDB 활성화 및 스키마 생성..."
docker compose exec -T postgres psql -U trading -d trading -c "CREATE EXTENSION IF NOT EXISTS timescaledb;" 2>/dev/null || true
# schema.sql은 docker-entrypoint-initdb.d에서 자동 실행되지만, 재설정 시 수동 실행
docker compose exec -T postgres psql -U trading -d trading -f /docker-entrypoint-initdb.d/01-schema.sql 2>/dev/null || true
# 테스트 테이블
docker compose exec -T postgres psql -U trading -d trading < "$PROJECT_DIR/db/schema_test.sql" 2>/dev/null || true
echo "      스키마 생성 완료 (메인 + 테스트)"

# 5. Python 패키지 설치
echo "[5/6] Python 패키지 설치..."
pip install -r "$PROJECT_DIR/requirements.txt"
echo "      패키지 설치 완료"

# 6. 초기 ETF 데이터 삽입
echo "[6/6] 초기 ETF 데이터 삽입..."
docker compose exec -T postgres psql -U trading -d trading <<'SQL'
INSERT INTO symbols (ticker, name, market, asset_type, sector, kis_code, is_active)
VALUES
    -- 시장 대표
    ('KODEX 200',           'KODEX 200',              'ETF', 'etf_equity',    'broad_market',      '069500', TRUE),
    ('TIGER 200',           'TIGER 200',              'ETF', 'etf_equity',    'broad_market',      '102110', TRUE),
    ('KODEX 코스닥150',      'KODEX 코스닥150',         'ETF', 'etf_equity',    'broad_market',      '229200', TRUE),
    ('TIGER 미국S&P500',     'TIGER 미국S&P500',       'ETF', 'etf_equity',    'broad_market',      '360750', TRUE),
    ('TIGER 미국나스닥100',    'TIGER 미국나스닥100',     'ETF', 'etf_equity',    'broad_market',      '133690', TRUE),

    -- AI/반도체
    ('KODEX 반도체',          'KODEX 반도체',            'ETF', 'etf_equity',    'ai_semiconductor',  '091160', TRUE),
    ('TIGER AI반도체핵심공정', 'TIGER AI반도체핵심공정',   'ETF', 'etf_equity',    'ai_semiconductor',  '469150', TRUE),
    ('TIGER 미국필라델피아반도체나스닥', 'TIGER 미국필라델피아반도체나스닥', 'ETF', 'etf_equity', 'ai_semiconductor', '381180', TRUE),

    -- 2차전지/클린에너지
    ('TIGER 2차전지테마',     'TIGER 2차전지테마',       'ETF', 'etf_equity',    'clean_energy',      '305540', TRUE),
    ('KODEX 2차전지산업',     'KODEX 2차전지산업',       'ETF', 'etf_equity',    'clean_energy',      '305720', TRUE),

    -- 바이오/헬스케어
    ('KODEX 바이오',          'KODEX 바이오',            'ETF', 'etf_equity',    'bio_healthcare',    '244580', TRUE),
    ('TIGER 헬스케어',        'TIGER 헬스케어',          'ETF', 'etf_equity',    'bio_healthcare',    '143860', TRUE),

    -- 금융/밸류업
    ('KODEX 은행',            'KODEX 은행',              'ETF', 'etf_equity',    'finance_valueup',   '091170', TRUE),
    ('TIGER 200금융',         'TIGER 200금융',           'ETF', 'etf_equity',    'finance_valueup',   '139270', TRUE),

    -- 방산
    ('TIGER 우주방산',        'TIGER 우주방산',          'ETF', 'etf_equity',    'defense',           '464520', TRUE),

    -- 채권
    ('KODEX 국고채10년',      'KODEX 국고채10년',        'ETF', 'etf_bond',      NULL,                '148070', TRUE),
    ('TIGER 단기채권',        'TIGER 단기채권',          'ETF', 'etf_bond',      NULL,                '157450', TRUE),
    ('KODEX 종합채권',        'KODEX 종합채권',          'ETF', 'etf_bond',      NULL,                '273130', TRUE),
    ('TIGER 미국채10년선물',   'TIGER 미국채10년선물',    'ETF', 'etf_bond',      NULL,                '305080', TRUE),
    ('ACE 미국30년국채',       'ACE 미국30년국채',        'ETF', 'etf_bond',      NULL,                '453850', TRUE),

    -- 금
    ('KODEX 골드선물(H)',     'KODEX 골드선물(H)',       'ETF', 'etf_gold',      NULL,                '132030', TRUE),
    ('ACE KRX금현물',         'ACE KRX금현물',           'ETF', 'etf_gold',      NULL,                '411060', TRUE)
ON CONFLICT (ticker) DO NOTHING;
SQL
echo "      ETF 데이터 삽입 완료"

echo ""
echo "=== 초기 설정 완료 ==="
echo "다음 단계:"
echo "  1. .env 파일에 API 키 등 민감정보 입력"
echo "  2. python -m app.main 으로 시스템 시작"
