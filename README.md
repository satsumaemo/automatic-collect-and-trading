# LLM 기반 다자산 자동매매 시스템

LLM(Gemini/Claude)을 활용한 한국/미국 ETF 자동매매 시스템.

## 아키텍처

모듈러 모놀리스 — 7개 서비스가 데이터 계약(`contracts.py`)으로 통신.

```
DataService → AnalysisService → SignalService → PortfolioService
                                                       ↓
                  MonitoringService ← ExecutionService ← RiskService
```

## 빠른 시작

```bash
# 1. 인프라 + 초기 설정
bash scripts/setup.sh

# 2. 환경변수 설정
vi .env

# 3. 시스템 시작
python -m app.main
```

## 기술 스택

- Python 3.11+, SQLAlchemy (async), PostgreSQL 16 + TimescaleDB, Redis 7
- LLM: Google Gemini (메인), Anthropic Claude (백업)
- 브로커: 한국투자증권 KIS API
