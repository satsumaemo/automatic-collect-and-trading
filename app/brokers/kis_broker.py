"""
KIS(한국투자증권) REST API 브로커.
BaseBroker를 상속하며, httpx.AsyncClient를 사용합니다.

토큰 관리: 24시간 만료, 만료 1시간 전 자동 갱신
레이트 리밋: 모든 API 호출 전 rate_limiter.acquire()
에러 처리: EGW00201(레이트리밋), OPSW0001(토큰만료) 자동 재시도
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import httpx

from app.brokers.base_broker import BaseBroker
from app.config import settings
from app.utils.rate_limiter import (
    rate_limiter,
    PRIORITY_ORDER_CHECK,
    PRIORITY_REALTIME,
    PRIORITY_DAILY,
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

# ── ETF 이름 → KIS 종목코드 매핑 ──
# symbols 테이블의 ticker(한글 이름)와 KIS API의 6자리 종목코드 매핑
ETF_KIS_CODE_MAP: Dict[str, str] = {
    # 시장 대표
    'KODEX 200': '069500',
    'TIGER 200': '102110',
    'KODEX 코스닥150': '229200',
    'TIGER 미국S&P500': '360750',
    'TIGER 미국나스닥100': '133690',
    # AI/반도체
    'KODEX 반도체': '091160',
    'TIGER AI반도체핵심공정': '469150',
    'TIGER 미국필라델피아반도체나스닥': '381180',
    # 2차전지/클린에너지
    'TIGER 2차전지테마': '305540',
    'KODEX 2차전지산업': '305720',
    # 바이오/헬스케어
    'KODEX 바이오': '244580',
    'TIGER 헬스케어': '143860',
    # 금융/밸류업
    'KODEX 은행': '091170',
    'TIGER 200금융': '139270',
    # 방산
    'TIGER 우주방산': '464520',
    # 채권
    'KODEX 국고채10년': '148070',
    'TIGER 단기채권': '157450',
    'KODEX 종합채권': '273130',
    'TIGER 미국채10년선물': '305080',
    'ACE 미국30년국채': '453850',
    # 금
    'KODEX 골드선물(H)': '132030',
    'ACE KRX금현물': '411060',
}


class KISBroker(BaseBroker):
    """한국투자증권 REST API 브로커"""

    def __init__(self) -> None:
        self._cfg = settings.kis
        self._base_url = self._cfg.base_url
        self._app_key = self._cfg.app_key
        self._app_secret = self._cfg.app_secret
        self._account_no = self._cfg.account_no
        self._account_product_code = self._cfg.account_product_code
        self._is_paper = settings.is_paper

        # 토큰 상태
        self._access_token: str = ""
        self._token_expires_at: Optional[datetime] = None

        # HTTP 클라이언트
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(30.0),
        )

        logger.info("KISBroker 초기화 [%s]", "모의투자" if self._is_paper else "실전")

    # ── 내부: 헤더 생성 ──

    def _headers(self, tr_id: str) -> Dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._access_token}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
        }

    # ── 토큰 관리 ──

    async def _ensure_token(self) -> None:
        """토큰이 없거나 만료 1시간 전이면 갱신"""
        if self._access_token and self._token_expires_at:
            if datetime.now() < self._token_expires_at - timedelta(hours=1):
                return
        await self.refresh_token()

    async def refresh_token(self) -> None:
        """접근 토큰 발급/갱신"""
        try:
            resp = await self._client.post(
                "/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._app_key,
                    "appsecret": self._app_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data["access_token"]
            # KIS 만료 형식: "2025-03-16 10:30:00"
            expires_str = data.get("access_token_token_expired", "")
            if expires_str:
                self._token_expires_at = datetime.strptime(
                    expires_str, "%Y-%m-%d %H:%M:%S"
                )
            else:
                self._token_expires_at = datetime.now() + timedelta(hours=23)
            logger.info("KIS 토큰 발급 완료 (만료: %s)", self._token_expires_at)
        except Exception as e:
            logger.error("KIS 토큰 발급 실패: %s", e)
            raise

    # ── 내부: API 호출 래퍼 ──

    async def _request(
        self,
        method: str,
        path: str,
        tr_id: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        priority: int = PRIORITY_DAILY,
    ) -> dict:
        """
        API 호출 래퍼. 레이트 리미터 + 토큰 검증 + 자동 재시도.
        """
        await self._ensure_token()

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await rate_limiter.acquire(priority)

                if method == "GET":
                    resp = await self._client.get(
                        path, headers=self._headers(tr_id), params=params
                    )
                else:
                    resp = await self._client.post(
                        path, headers=self._headers(tr_id), json=json_body
                    )

                resp.raise_for_status()
                data = resp.json()

                # KIS 에러 코드 확인
                rt_cd = data.get("rt_cd", "0")
                msg_cd = data.get("msg_cd", "")

                if rt_cd != "0":
                    # 레이트 리밋 초과
                    if msg_cd == "EGW00201":
                        logger.warning("레이트 리밋 초과, 1초 대기 후 재시도 (%d/%d)", attempt, MAX_RETRIES)
                        import asyncio
                        await asyncio.sleep(1.0)
                        continue
                    # 토큰 만료
                    if msg_cd == "OPSW0001":
                        logger.warning("토큰 만료, 갱신 후 재시도 (%d/%d)", attempt, MAX_RETRIES)
                        await self.refresh_token()
                        continue
                    # 기타 에러
                    msg = data.get("msg1", "Unknown error")
                    logger.error("KIS API 에러 [%s]: %s", msg_cd, msg)
                    raise RuntimeError(f"KIS API 에러 [{msg_cd}]: {msg}")

                return data

            except httpx.HTTPStatusError as e:
                # 모의투자 서버는 500 에러가 흔함 — WARNING 레벨
                logger.warning("KIS HTTP 에러 (%d/%d): %s", attempt, MAX_RETRIES, e.response.status_code)
                if attempt == MAX_RETRIES:
                    raise
                import asyncio
                await asyncio.sleep(1.0)
            except httpx.RequestError as e:
                logger.warning("KIS 요청 에러 (%d/%d): %s", attempt, MAX_RETRIES, e)
                if attempt == MAX_RETRIES:
                    raise
                import asyncio
                await asyncio.sleep(1.0)

        raise RuntimeError("KIS API 최대 재시도 초과")

    # ── 종목코드 변환 ──

    def resolve_kis_code(self, ticker: str) -> str:
        """ticker(ETF 이름) → KIS 6자리 종목코드"""
        code = ETF_KIS_CODE_MAP.get(ticker)
        if code:
            return code
        # ticker가 이미 숫자 코드이면 그대로 반환
        if ticker.isdigit() and len(ticker) == 6:
            return ticker
        raise ValueError(f"KIS 종목코드를 찾을 수 없음: {ticker}")

    # ── BaseBroker 구현 ──

    async def get_daily_ohlcv(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
    ) -> List[dict]:
        """
        일봉 데이터 조회.
        start_date, end_date: 'YYYYMMDD' 형식
        """
        kis_code = self.resolve_kis_code(ticker)
        data = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            tr_id="FHKST03010100",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": kis_code,
                "FID_INPUT_DATE_1": start_date,
                "FID_INPUT_DATE_2": end_date,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
            priority=PRIORITY_DAILY,
        )

        output2 = data.get("output2", [])
        if not output2:
            logger.warning("일봉 데이터 없음: %s (%s~%s)", ticker, start_date, end_date)
            return []

        rows = []
        for item in output2:
            # 빈 항목 건너뛰기
            if not item.get("stck_bsop_date"):
                continue
            rows.append({
                "date": item["stck_bsop_date"],
                "open": float(item.get("stck_oprc", 0)),
                "high": float(item.get("stck_hgpr", 0)),
                "low": float(item.get("stck_lwpr", 0)),
                "close": float(item.get("stck_clpr", 0)),
                "volume": int(item.get("acml_vol", 0)),
                "turnover": int(item.get("acml_tr_pbmn", 0)),
            })

        return rows

    async def get_market_price(self, ticker: str) -> float:
        """현재가 조회"""
        kis_code = self.resolve_kis_code(ticker)
        data = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": kis_code,
            },
            priority=PRIORITY_REALTIME,
        )
        output = data.get("output", {})
        return float(output.get("stck_prpr", 0))

    async def get_market_price_detail(self, ticker: str) -> dict:
        """현재가 + 거래량 상세 조회"""
        kis_code = self.resolve_kis_code(ticker)
        data = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": kis_code,
            },
            priority=PRIORITY_REALTIME,
        )
        output = data.get("output", {})
        return {
            "price": float(output.get("stck_prpr", 0)),
            "volume": int(output.get("acml_vol", 0)),
        }

    async def get_balance(self) -> dict:
        """계좌 잔고 조회"""
        cano = self._account_no[:8]
        acnt_prdt_cd = self._account_no[8:] if len(self._account_no) > 8 else self._account_product_code

        tr_id = "VTTC8434R" if self._is_paper else "TTTC8434R"
        data = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id=tr_id,
            params={
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
            priority=PRIORITY_ORDER_CHECK,
        )

        # 종목별 보유 상세
        positions = []
        for item in data.get("output1", []):
            if int(item.get("hldg_qty", 0)) == 0:
                continue
            positions.append({
                "ticker": item.get("pdno", ""),
                "name": item.get("prdt_name", ""),
                "quantity": int(item.get("hldg_qty", 0)),
                "avg_price": float(item.get("pchs_avg_pric", 0)),
                "current_price": float(item.get("prpr", 0)),
                "eval_amount": int(item.get("evlu_amt", 0)),
                "pnl": int(item.get("evlu_pfls_amt", 0)),
                "pnl_pct": float(item.get("evlu_pfls_rt", 0)),
            })

        # 계좌 전체
        output2 = data.get("output2", [{}])
        summary = output2[0] if output2 else {}

        return {
            "positions": positions,
            "total_eval": int(summary.get("tot_evlu_amt", 0)),
            "cash": int(summary.get("dnca_tot_amt", 0)),
            "purchase_amount": int(summary.get("pchs_amt_smtl_amt", 0)),
            "eval_pnl": int(summary.get("evlu_pfls_smtl_amt", 0)),
        }

    async def get_positions(self) -> List[dict]:
        """보유 종목 목록 조회"""
        balance = await self.get_balance()
        return balance["positions"]

    async def submit_order(self, order: dict) -> dict:
        """
        매수/매도 주문 제출.
        order: {"ticker", "side" (buy/sell), "quantity", "price" (None이면 시장가)}
        """
        kis_code = self.resolve_kis_code(order["ticker"])
        cano = self._account_no[:8]
        acnt_prdt_cd = self._account_no[8:] if len(self._account_no) > 8 else self._account_product_code

        side = order["side"]
        if side == "buy":
            tr_id = "VTTC0802U" if self._is_paper else "TTTC0802U"
        else:
            tr_id = "VTTC0801U" if self._is_paper else "TTTC0801U"

        # 시장가/지정가
        price = order.get("price")
        if price is None or price == 0:
            ord_dvsn = "01"  # 시장가
            ord_unpr = "0"
        else:
            ord_dvsn = "00"  # 지정가
            ord_unpr = str(int(price))

        data = await self._request(
            "POST",
            "/uapi/domestic-stock/v1/trading/order-cash",
            tr_id=tr_id,
            json_body={
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "PDNO": kis_code,
                "ORD_DVSN": ord_dvsn,
                "ORD_QTY": str(order["quantity"]),
                "ORD_UNPR": ord_unpr,
            },
            priority=PRIORITY_ORDER_CHECK,
        )

        output = data.get("output", {})
        return {
            "order_id": output.get("ODNO", ""),
            "order_time": output.get("ORD_TMD", ""),
        }

    async def cancel_order(self, order_id: str) -> bool:
        """
        주문 취소.
        Returns True=취소 성공, False=취소 실패.
        Raises RuntimeError with 'already_filled' in message if order was already filled.
        """
        cano = self._account_no[:8]
        acnt_prdt_cd = self._account_no[8:] if len(self._account_no) > 8 else self._account_product_code

        tr_id = "VTTC0803U" if self._is_paper else "TTTC0803U"
        try:
            await self._request(
                "POST",
                "/uapi/domestic-stock/v1/trading/order-rvsecncl",
                tr_id=tr_id,
                json_body={
                    "CANO": cano,
                    "ACNT_PRDT_CD": acnt_prdt_cd,
                    "KRX_FWDG_ORD_ORGNO": "",
                    "ORGN_ODNO": order_id,
                    "ORD_DVSN": "00",
                    "RVSE_CNCL_DVSN_CD": "02",  # 02=취소
                    "ORD_QTY": "0",              # 전량
                    "ORD_UNPR": "0",
                    "QTY_ALL_ORD_YN": "Y",
                },
                priority=PRIORITY_ORDER_CHECK,
            )
            return True
        except RuntimeError as e:
            err_msg = str(e)
            # "취소할 수량이 없습니다" → 이미 체결 완료
            if "취소" in err_msg and "수량" in err_msg:
                logger.info("주문 이미 체결됨 (취소 불가) [%s]: %s", order_id, err_msg)
                raise RuntimeError(f"already_filled: {order_id}")
            logger.error("주문 취소 실패 [%s]: %s", order_id, e)
            return False
        except Exception as e:
            logger.error("주문 취소 실패 [%s]: %s", order_id, e)
            return False

    async def get_order_status(self, order_id: str) -> dict:
        """주문 상태 조회"""
        cano = self._account_no[:8]
        acnt_prdt_cd = self._account_no[8:] if len(self._account_no) > 8 else self._account_product_code

        tr_id = "VTTC8001R" if self._is_paper else "TTTC8001R"
        data = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            tr_id=tr_id,
            params={
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "INQR_STRT_DT": datetime.now().strftime("%Y%m%d"),
                "INQR_END_DT": datetime.now().strftime("%Y%m%d"),
                "SLL_BUY_DVSN_CD": "00",
                "INQR_DVSN": "00",
                "PDNO": "",
                "CCLD_DVSN": "00",
                "ORD_GNO_BRNO": "",
                "ODNO": order_id,
                "INQR_DVSN_3": "00",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
            priority=PRIORITY_ORDER_CHECK,
        )

        for item in data.get("output1", []):
            if item.get("odno") == order_id:
                ord_qty = int(item.get("ord_qty", 0))
                tot_ccld_qty = int(item.get("tot_ccld_qty", 0))
                avg_price = float(item.get("avg_prvs", 0))

                # 상태 판정: 체결수량 기반
                if tot_ccld_qty == 0:
                    status = "pending"
                elif tot_ccld_qty >= ord_qty:
                    status = "filled"
                else:
                    status = "partial"

                return {
                    "order_id": order_id,
                    "ticker": item.get("pdno", ""),
                    "side": "buy" if item.get("sll_buy_dvsn_cd") == "02" else "sell",
                    "quantity": ord_qty,
                    "filled_quantity": tot_ccld_qty,
                    "filled_price": avg_price,
                    "status": status,
                }

        return {"order_id": order_id, "status": "unknown"}

    def is_market_open(self) -> bool:
        """한국 주식시장 개장 여부 (평일 09:00~15:30 KST)"""
        import pytz
        kst = pytz.timezone("Asia/Seoul")
        now = datetime.now(kst)

        # 주말
        if now.weekday() >= 5:
            return False

        # 시간 확인 (09:00 ~ 15:30)
        market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

        return market_open <= now <= market_close

    async def close(self) -> None:
        """HTTP 클라이언트 종료"""
        await self._client.aclose()
