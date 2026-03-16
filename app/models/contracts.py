"""
서비스 간 데이터 계약.
모든 서비스가 주고받는 데이터를 dataclass와 Enum으로 정의합니다.
이것이 모듈러 모놀리스의 핵심입니다.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Union, Tuple
from datetime import datetime
from enum import Enum


# ═══════════════════════════════════════
# 열거형 (Enum)
# ═══════════════════════════════════════

class Regime(Enum):
    """시장 레짐 — LLM이 판단"""
    EXPANSION = 'expansion'   # 확장기
    SLOWDOWN = 'slowdown'     # 둔화기
    WARNING = 'warning'       # 경고기
    CRISIS = 'crisis'         # 위기기


class AlertLevel(Enum):
    """경고 레벨 — 규칙 기반 판단"""
    NORMAL = 'normal'         # 정상
    CAUTION = 'caution'       # 주의
    WARNING = 'warning'       # 경고
    EMERGENCY = 'emergency'   # 비상


class ConvergenceType(Enum):
    """수렴 유형"""
    STRONG = 'strong_convergence'    # 강한 수렴 (단기+중장기 모두 강함)
    WEAK = 'weak_convergence'        # 약한 수렴 (양쪽 보통 이상)
    SHORT_ONLY = 'short_only'        # 단기만 강함 (밈주식 위험)
    LONG_ONLY = 'long_only_waiting'  # 중장기만 강함 (대기)
    NONE = 'no_signal'               # 신호 없음


class OrderSide(Enum):
    BUY = 'buy'
    SELL = 'sell'


class OrderTrigger(Enum):
    REBALANCE = 'rebalance'
    SIGNAL = 'signal'
    STOP_LOSS = 'stop_loss'
    EMERGENCY = 'emergency'
    KILL_SWITCH = 'kill_switch'


# ═══════════════════════════════════════
# DataService 출력
# ═══════════════════════════════════════

@dataclass
class PriceData:
    ticker: str
    price: float
    volume: int
    timestamp: datetime


@dataclass
class OHLCVData:
    """일봉 데이터"""
    ticker: str
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    adj_close: Optional[float] = None


@dataclass
class IndicatorData:
    """기술적 지표"""
    ticker: str
    date: datetime
    ma5: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    ma120: Optional[float] = None
    rsi14: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_lower: Optional[float] = None
    atr14: Optional[float] = None
    adx14: Optional[float] = None
    obv: Optional[int] = None


@dataclass
class MacroSnapshot:
    """거시경제 지표 스냅샷"""
    date: datetime
    vix: float
    us10y: float
    us2y: float
    yield_spread: float          # 10Y - 2Y
    hy_spread: float             # 하이일드 스프레드
    hy_spread_percentile: float  # 10년 백분위
    fed_rate: float
    cpi_latest: float
    usdkrw: float
    fear_greed: Optional[float] = None
    fsi: Optional[float] = None  # 금융스트레스지수


@dataclass
class NewsSummary:
    """뉴스 요약 (LLM 입력용)"""
    date: datetime
    total_articles_24h: int
    top_buzzing_topics: List[dict]
    key_articles: List[dict]
    sector_summary: dict


@dataclass
class BuzzData:
    """뉴스 빈도 변화"""
    category: str
    buzz_score: float          # 최근 7일/30일 평균 비율
    tone_shift: float          # 감성 변화량
    current_tone: float        # 현재 평균 감성 (-1~+1)


@dataclass
class EconomicEvent:
    """경제 이벤트"""
    name: str
    date: datetime
    importance: str            # critical, high, medium
    hours_away: float


@dataclass
class ETFFlowData:
    """ETF 자금흐름"""
    sector: str
    net_flow_7d: float
    net_flow_30d_avg: float
    flow_ratio: float          # 7일/30일평균 비율


@dataclass
class SupplyDemandData:
    """수급 데이터"""
    ticker: str
    date: datetime
    foreign_net: int           # 외국인 순매매 금액
    institution_net: int       # 기관 순매매
    individual_net: int        # 개인 순매매


@dataclass
class FinancialData:
    """재무제표 요약"""
    company_id: int
    ticker: str
    fiscal_year: int
    fiscal_quarter: int
    revenue: int
    operating_income: int
    net_income: int
    roe: float
    debt_ratio: float
    operating_margin: float
    fcf: int
    quality_score: int         # 0~100
    revenue_yoy: Optional[float] = None
    op_income_yoy: Optional[float] = None


@dataclass
class PolymarketSignal:
    """폴리마켓 이상 신호"""
    market_title: str
    signal_type: str           # prob_spike, abnormal_volume, insider_pattern
    severity: str              # caution, warning, emergency, critical
    prob_before: float
    prob_after: float
    volume_ratio: float
    detail: str


@dataclass
class InsiderTrade:
    """내부자 거래"""
    company_id: int
    insider_name: str
    position: str
    trade_type: str
    shares: int
    price: float
    total_value: int
    trade_date: datetime
    report_date: datetime


# ═══════════════════════════════════════
# AnalysisService 출력
# ═══════════════════════════════════════

@dataclass
class RegimeAnalysis:
    """LLM 시장 레짐 판단 결과"""
    regime: Regime
    confidence: float          # 0.0~1.0
    reasoning: str
    asset_allocation_suggestion: Dict[str, float]
    # {'kr_equity': 35, 'us_equity': 35, 'kr_bond': 5, ...}
    sector_outlook: List[dict]
    key_risks: List[dict]
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class RiskDetection:
    """LLM 위험 감지 결과"""
    alert_level: AlertLevel
    confidence: float
    detected_signals: List[dict]
    recommended_actions: List[dict]


@dataclass
class NewsAnalysis:
    """뉴스 동향 분석 결과"""
    market_tone: str           # bullish, neutral, bearish
    tone_confidence: float
    sector_flows: List[dict]
    risk_events: List[dict]
    tone_shift: dict


@dataclass
class SentimentResult:
    """개별 뉴스 감성분석"""
    article_id: int
    sentiment: float           # -1.0 ~ +1.0
    reasoning: str


@dataclass
class FinancialAnalysis:
    """재무제표 분석 결과"""
    company_id: int
    overall_score: int         # 0~100
    investment_grade: str      # A, B, C, D, F
    key_risks: List[str]
    key_strengths: List[str]
    confidence: float


@dataclass
class ValidationResult:
    """LLM 사전검증 결과 (거래 전 AI 검문소)"""
    decision: str              # approve, conditional_approve, reject, defer
    confidence: float
    checks: dict               # 5개 검사 결과
    size_reduction_pct: int    # 100=변경없음, 50=절반
    risk_summary: str = ''
    defer_until: Optional[datetime] = None


@dataclass
class PostTradeReview:
    """일일 거래 사후 리뷰"""
    date: datetime
    total_trades: int
    flagged_trades: List[dict]
    overall_assessment: str
    improvement_suggestions: List[str]


# ═══════════════════════════════════════
# SignalService 출력
# ═══════════════════════════════════════

@dataclass
class ConvergenceResult:
    """섹터별 수렴 판단 결과"""
    sector: str
    short_term_score: float    # 0~100
    long_term_score: float     # 0~100
    convergence_type: ConvergenceType
    confidence: float
    recommended_etfs: List[str]
    position_multiplier: float


@dataclass
class TradingSignals:
    """최종 거래 신호 패키지 (→ PortfolioService)"""
    date: datetime
    regime: RegimeAnalysis
    convergence_results: List[ConvergenceResult]
    alert_level: AlertLevel
    default_etfs: List[str]


# ═══════════════════════════════════════
# PortfolioService → RiskService
# ═══════════════════════════════════════

@dataclass
class ProposedOrder:
    """주문 제안 (아직 실행 전, 리스크 승인 필요)"""
    ticker: str
    side: OrderSide
    quantity: int
    price: Optional[float]     # None이면 시장가
    amount: int                # 총 금액
    trigger: OrderTrigger
    reason: str
    sector: Optional[str] = None


# ═══════════════════════════════════════
# RiskService 출력
# ═══════════════════════════════════════

@dataclass
class KillSwitchResult:
    """Kill Switch 점검 결과"""
    passed: bool
    violated_rules: List[str]
    details: dict


@dataclass
class EntryFilterResult:
    """진입 필터 결과"""
    passed: bool
    reason: str
    details: dict


# ═══════════════════════════════════════
# RiskService → ExecutionService
# ═══════════════════════════════════════

@dataclass
class ApprovedOrder:
    """리스크 승인된 주문 (실행 가능)"""
    ticker: str
    side: OrderSide
    quantity: int
    price: Optional[float]
    amount: int
    trigger: OrderTrigger
    reason: str
    approved_at: datetime = field(default_factory=datetime.now)
    validation_id: Optional[int] = None
    size_modified: bool = False
    original_quantity: Optional[int] = None

    @classmethod
    def from_proposed(cls, order: ProposedOrder) -> "ApprovedOrder":
        return cls(
            ticker=order.ticker,
            side=order.side,
            quantity=order.quantity,
            price=order.price,
            amount=order.amount,
            trigger=order.trigger,
            reason=order.reason,
        )


@dataclass
class RejectedOrder:
    """리스크 거부된 주문"""
    original_order: ProposedOrder
    rejected_at: datetime = field(default_factory=datetime.now)
    rejected_by: str = ''      # kill_switch, llm_validation, entry_filter
    reason: str = ''


# ═══════════════════════════════════════
# ExecutionService → MonitoringService
# ═══════════════════════════════════════

@dataclass
class ExecutionResult:
    """주문 체결 결과"""
    order_id: str
    ticker: str
    side: OrderSide
    quantity: int
    filled_quantity: int
    filled_price: float
    status: str                # filled, partial, cancelled, rejected
    commission: float
    tax: float
    slippage_pct: float
    trigger: OrderTrigger
    timestamp: datetime = field(default_factory=datetime.now)


# ═══════════════════════════════════════
# MonitoringService 관련
# ═══════════════════════════════════════

@dataclass
class DailyReport:
    """일일 성과 리포트"""
    date: datetime
    portfolio_value: int
    daily_return: float
    cumulative_return: float
    current_drawdown: float
    max_drawdown: float
    sharpe_ratio: Optional[float]
    equity_pct: float
    bond_pct: float
    gold_pct: float
    cash_pct: float
    trade_count: int
    regime: Regime
    alert_level: AlertLevel


# ═══════════════════════════════════════
# 섹터/ETF 매핑 (상수)
# ═══════════════════════════════════════

SECTOR_ETF_MAPPING: Dict[str, dict] = {
    'ai_semiconductor': {
        'kr_etfs': ['KODEX 반도체', 'TIGER AI반도체핵심공정'],
        'us_etfs': ['TIGER 미국필라델피아반도체나스닥'],
        'keywords': ['AI', '반도체', 'GPU', '엔비디아', 'HBM'],
    },
    'clean_energy': {
        'kr_etfs': ['TIGER 2차전지테마', 'KODEX 2차전지산업'],
        'us_etfs': ['TIGER 미국클린에너지'],
        'keywords': ['배터리', '전기차', '태양광', '2차전지'],
    },
    'bio_healthcare': {
        'kr_etfs': ['KODEX 바이오', 'TIGER 헬스케어'],
        'us_etfs': ['TIGER 미국나스닥바이오'],
        'keywords': ['바이오', '신약', '임상', 'FDA'],
    },
    'finance_valueup': {
        'kr_etfs': ['KODEX 은행', 'TIGER 200금융'],
        'keywords': ['밸류업', '배당', '자사주', '은행'],
    },
    'defense': {
        'kr_etfs': ['TIGER 우주방산'],
        'keywords': ['방산', '방위', '국방', '무기'],
    },
    'broad_market': {
        'kr_etfs': ['KODEX 200', 'TIGER 200'],
        'us_etfs': ['TIGER 미국S&P500', 'TIGER 미국나스닥100'],
        'keywords': [],
    },
}

# 레짐별 기본 자산배분 (%)
REGIME_ALLOCATION: Dict[Regime, dict] = {
    Regime.EXPANSION: {
        'kr_equity': 35, 'us_equity': 35,
        'kr_bond': 5, 'us_bond': 5,
        'gold': 5, 'cash_rp': 15,
        'bond_duration': 'short',
    },
    Regime.SLOWDOWN: {
        'kr_equity': 20, 'us_equity': 25,
        'kr_bond': 15, 'us_bond': 15,
        'gold': 10, 'cash_rp': 15,
        'bond_duration': 'medium',
    },
    Regime.WARNING: {
        'kr_equity': 10, 'us_equity': 15,
        'kr_bond': 15, 'us_bond': 20,
        'gold': 15, 'cash_rp': 25,
        'bond_duration': 'long',
    },
    Regime.CRISIS: {
        'kr_equity': 5, 'us_equity': 5,
        'kr_bond': 10, 'us_bond': 25,
        'gold': 15, 'cash_rp': 40,
        'bond_duration': 'long',
    },
}
