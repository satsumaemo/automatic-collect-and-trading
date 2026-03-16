"""
환경 설정 모듈.
TRADING_MODE 환경변수 하나로 모의투자/실전을 전환합니다.
모든 민감 정보는 환경변수에서 로드하며 절대 하드코딩하지 않습니다.
"""

import os
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


# ═══════════════════════════════════════
# 트레이딩 모드
# ═══════════════════════════════════════

TRADING_MODE: str = os.getenv("TRADING_MODE", "paper")  # 'paper' 또는 'live'


# ═══════════════════════════════════════
# KIS API 설정
# ═══════════════════════════════════════

@dataclass(frozen=True)
class KISConfig:
    """한국투자증권 API 설정"""
    base_url: str
    rate_limit: int          # 초당 요청 수
    app_key: str
    app_secret: str
    account_no: str
    account_product_code: str

    @staticmethod
    def from_env(mode: str) -> "KISConfig":
        if mode == "paper":
            return KISConfig(
                base_url="https://openapivts.koreainvestment.com:29443",
                rate_limit=5,
                app_key=os.getenv("KIS_PAPER_APP_KEY", ""),
                app_secret=os.getenv("KIS_PAPER_APP_SECRET", ""),
                account_no=os.getenv("KIS_PAPER_ACCOUNT_NO", ""),
                account_product_code=os.getenv("KIS_PAPER_ACCOUNT_PRODUCT_CODE", "01"),
            )
        else:
            return KISConfig(
                base_url="https://openapi.koreainvestment.com:9443",
                rate_limit=20,
                app_key=os.getenv("KIS_LIVE_APP_KEY", ""),
                app_secret=os.getenv("KIS_LIVE_APP_SECRET", ""),
                account_no=os.getenv("KIS_LIVE_ACCOUNT_NO", ""),
                account_product_code=os.getenv("KIS_LIVE_ACCOUNT_PRODUCT_CODE", "01"),
            )


# ═══════════════════════════════════════
# DB 설정
# ═══════════════════════════════════════

@dataclass(frozen=True)
class PostgresConfig:
    host: str = os.getenv("POSTGRES_HOST", "localhost")
    port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    db: str = os.getenv("POSTGRES_DB", "trading")
    user: str = os.getenv("POSTGRES_USER", "trading")
    password: str = os.getenv("POSTGRES_PASSWORD", "")

    @property
    def dsn(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"

    @property
    def sync_dsn(self) -> str:
        return f"postgresql+psycopg2://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"


@dataclass(frozen=True)
class RedisConfig:
    host: str = os.getenv("REDIS_HOST", "localhost")
    port: int = int(os.getenv("REDIS_PORT", "6379"))
    db: int = int(os.getenv("REDIS_DB", "0"))
    password: Optional[str] = os.getenv("REDIS_PASSWORD", None)

    @property
    def url(self) -> str:
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"


# ═══════════════════════════════════════
# LLM 설정
# ═══════════════════════════════════════

@dataclass(frozen=True)
class LLMConfig:
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")             # 메인
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")       # 백업
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")             # 백업


# ═══════════════════════════════════════
# 외부 데이터 API 설정
# ═══════════════════════════════════════

@dataclass(frozen=True)
class DataAPIConfig:
    fred_api_key: str = os.getenv("FRED_API_KEY", "")                 # FRED (선택)


# ═══════════════════════════════════════
# 텔레그램 설정
# ═══════════════════════════════════════

@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")


# ═══════════════════════════════════════
# 트레이딩 파라미터 (상수)
# ═══════════════════════════════════════

@dataclass(frozen=True)
class TradingParams:
    # 현금/리밸런싱
    min_cash_ratio: float = 0.05              # 최소현금유지비율 5%
    rebalance_min_interval_days: int = 14     # 리밸런싱 최소 간격
    rebalance_drift_threshold: float = 0.05   # 리밸런싱 드리프트 임계 5%p
    min_trade_amount: int = 100_000           # 최소거래금액 100,000원

    # 비중 제한
    max_single_stock_weight: float = 0.20     # 단일종목 최대비중 20%
    max_single_sector_weight: float = 0.35    # 단일섹터 최대비중 35%
    max_total_equity_weight: float = 0.80     # 총주식 최대비중 80%


# ═══════════════════════════════════════
# 리스크 파라미터 (상수, Kill Switch)
# ═══════════════════════════════════════

@dataclass(frozen=True)
class RiskParams:
    # 개별종목 손절
    stop_loss_normal: float = -0.07           # 정상 시 -7%
    stop_loss_caution: float = -0.05          # 주의/경고 시 -5%

    # 포트폴리오 한도
    portfolio_daily_limit: float = -0.03      # 일일 -3%
    portfolio_weekly_limit: float = -0.05     # 주간 -5%
    portfolio_monthly_limit: float = -0.10    # 월간 -10%

    # 냉각 기간
    cooling_period_days: int = 30             # 냉각 기간 30일

    # 거래 제한
    max_daily_trades: int = 50                # 일일 최대 거래횟수
    max_daily_turnover: float = 0.30          # 일일 최대 회전율 30%
    max_single_order_ratio: float = 0.10      # 단일주문 최대비율 10%


# ═══════════════════════════════════════
# 통합 설정 객체
# ═══════════════════════════════════════

@dataclass
class Settings:
    """모든 설정을 하나로 묶는 최상위 객체"""
    mode: str = TRADING_MODE
    kis: KISConfig = field(default_factory=lambda: KISConfig.from_env(TRADING_MODE))
    postgres: PostgresConfig = field(default_factory=PostgresConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    data_api: DataAPIConfig = field(default_factory=DataAPIConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    trading: TradingParams = field(default_factory=TradingParams)
    risk: RiskParams = field(default_factory=RiskParams)

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"


# 전역 설정 싱글턴
settings = Settings()
