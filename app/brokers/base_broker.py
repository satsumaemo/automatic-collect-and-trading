"""
BaseBroker — 브로커 인터페이스 (ABC).

KIS, 키움 등 어떤 브로커든 이 인터페이스를 구현하면 교체 가능합니다.
"""

from abc import ABC, abstractmethod
from typing import Dict, List


class BaseBroker(ABC):
    """브로커 추상 기반 클래스"""

    @abstractmethod
    async def submit_order(self, order: dict) -> dict:
        """주문 제출 → 주문 ID 포함 결과 반환"""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """주문 취소 → 성공 여부"""
        ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> dict:
        """주문 상태 조회"""
        ...

    @abstractmethod
    async def get_balance(self) -> dict:
        """계좌 잔고 조회 (현금, 총자산 등)"""
        ...

    @abstractmethod
    async def get_positions(self) -> List[dict]:
        """보유 종목 목록 조회"""
        ...

    @abstractmethod
    async def get_market_price(self, ticker: str) -> float:
        """현재가 조회"""
        ...

    @abstractmethod
    async def refresh_token(self) -> None:
        """인증 토큰 갱신"""
        ...

    @abstractmethod
    def is_market_open(self) -> bool:
        """현재 장 운영 중인지 확인"""
        ...
