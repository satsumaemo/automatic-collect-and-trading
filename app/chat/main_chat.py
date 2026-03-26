"""투자 토론 채팅 — Streamlit 메인 앱"""

import logging
import streamlit as st

from app.chat.db_reader import DBReader
from app.chat.context_builder import ContextBuilder
from app.chat.gemini_chat import GeminiChat

logger = logging.getLogger(__name__)


def main():
    st.set_page_config(
        page_title="투자 토론방",
        page_icon="💹",
        layout="wide",
    )

    # ── 세션 상태 초기화 ──
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "db" not in st.session_state:
        st.session_state.db = DBReader()
    if "context_builder" not in st.session_state:
        st.session_state.context_builder = ContextBuilder(st.session_state.db)
    if "chat" not in st.session_state:
        st.session_state.chat = GeminiChat()
        system_prompt = st.session_state.context_builder.build_system_prompt()
        st.session_state.chat.start_session(system_prompt)
    if "sidebar_data" not in st.session_state:
        st.session_state.sidebar_data = _load_sidebar_data(st.session_state.db)

    # ── 사이드바 ──
    _render_sidebar(st.session_state.sidebar_data, st.session_state.db)

    # ── 메인 영역 ──
    st.title("💬 투자 토론")
    st.caption("수집된 뉴스·시세·LLM 분석결과를 바탕으로 Gemini와 투자 아이디어를 토론합니다")

    # 대화 이력 표시
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # 빠른 질문 버튼 (대화가 비어있을 때만)
    if not st.session_state.messages:
        _render_quick_buttons()

    # 채팅 입력
    if prompt := st.chat_input("투자에 대해 무엇이든 물어보세요..."):
        _handle_user_input(prompt)


# ── 헬퍼 함수 ──


def _load_sidebar_data(db: DBReader) -> dict:
    data = {}

    # 시장 레짐
    try:
        data["regime"] = db.get_latest_regime()
    except Exception as e:
        logger.warning("레짐 로드 실패: %s", e)
        data["regime"] = {"regime": "unknown", "confidence": 0}

    # 보유 포지션 — KIS API → Redis → trade_history 폴백
    try:
        pos_result = db.get_current_positions()
        data["positions"] = pos_result
        logger.info(
            "사이드바 포지션 로드 성공 (source=%s, %d개)",
            pos_result.get("source", "?"),
            len(pos_result.get("positions", [])),
        )
    except Exception as e:
        logger.error("사이드바 포지션 로드 실패: %s", e, exc_info=True)
        data["positions"] = {"positions": [], "cash": 0, "total_eval": 0, "source": "error"}

    # 뉴스
    try:
        data["news"] = db.get_recent_news(days=1, limit=5)
    except Exception as e:
        logger.warning("뉴스 로드 실패: %s", e)
        data["news"] = []

    # 일일 성과
    try:
        data["performance"] = db.get_daily_performance(days=7)
    except Exception as e:
        logger.warning("성과 로드 실패: %s", e)
        data["performance"] = []

    return data


def _render_sidebar(data: dict, db: DBReader):
    with st.sidebar:
        st.header("💹 투자 토론방")
        st.divider()

        # 시장 레짐
        regime = data.get("regime", {})
        regime_name = regime.get("regime", "unknown")
        confidence = regime.get("confidence", 0)
        regime_icons = {
            "expansion": "🟢", "slowdown": "🟡",
            "warning": "🟠", "crisis": "🔴", "unknown": "⚪",
        }
        icon = regime_icons.get(regime_name, "⚪")
        st.subheader("📊 시장 레짐")
        if isinstance(confidence, (int, float)) and confidence <= 1:
            st.markdown(f"{icon} **{regime_name}** (확신도: {confidence:.0%})")
        else:
            st.markdown(f"{icon} **{regime_name}** (확신도: {confidence}%)")

        # 자산배분 제안
        allocation = regime.get("asset_allocation_suggestion", {})
        if allocation:
            st.subheader("⚖️ 자산배분 제안")
            cols = st.columns(2)
            for i, (k, v) in enumerate(allocation.items()):
                cols[i % 2].metric(k, f"{v}%")

        st.divider()

        # 보유 포지션
        pos_data = data.get("positions", {})
        if isinstance(pos_data, dict):
            pos_list = pos_data.get("positions", [])
            cash = pos_data.get("cash", 0)
            total_eval = pos_data.get("total_eval", 0)
            eval_pnl = pos_data.get("eval_pnl", 0)
            source = pos_data.get("source", "")
        else:
            pos_list = pos_data if isinstance(pos_data, list) else []
            cash = 0
            total_eval = 0
            eval_pnl = 0
            source = ""
        st.subheader(f"📋 보유 포지션 ({len(pos_list)}개)")
        if pos_list:
            for pos in pos_list[:8]:
                ticker = pos.get("ticker", "?")
                name = pos.get("name", "")
                display = name if name else ticker
                qty = pos.get("quantity", 0)
                pnl = pos.get("pnl_pct", 0)
                eval_amt = pos.get("eval_amount", 0)
                p_icon = "🟢" if pnl >= 0 else "🔴"
                st.markdown(f"{p_icon} **{display}** ({qty}주) {pnl:+.1f}%")
                if eval_amt:
                    st.caption(f"　평가금액: {eval_amt:,.0f}원")
        else:
            st.caption("현재 보유 종목 없음")
        if total_eval:
            st.metric("총 평가", f"{total_eval:,.0f}원",
                       delta=f"{eval_pnl:+,.0f}원" if eval_pnl else None)
        if cash:
            st.caption(f"💰 예수금: {cash:,.0f}원")

        st.divider()

        # 오늘의 뉴스
        news = data.get("news", [])
        st.subheader("📰 오늘의 뉴스")
        if news:
            for n in news[:5]:
                source = n.get("source", "")
                title = n.get("title", "")[:60]
                st.caption(f"[{source}] {title}")
        else:
            st.caption("오늘 수집된 뉴스 없음")

        st.divider()

        # 버튼
        if st.button("🔄 새 대화 시작", use_container_width=True):
            st.session_state.messages = []
            st.session_state.chat.reset()
            system_prompt = st.session_state.context_builder.build_system_prompt()
            st.session_state.chat.start_session(system_prompt)
            st.rerun()

        if st.button("🔃 데이터 새로고침", use_container_width=True):
            st.session_state.sidebar_data = _load_sidebar_data(db)
            system_prompt = st.session_state.context_builder.build_system_prompt()
            st.session_state.chat.reset()
            st.session_state.chat.start_session(system_prompt)
            st.session_state.messages = []
            st.rerun()


def _render_quick_buttons():
    st.markdown("##### 💡 빠른 질문")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📊 현재 시장 분석", use_container_width=True):
            _handle_user_input("현재 시장 상황을 종합적으로 분석해줘")
        if st.button("📰 주목할 뉴스", use_container_width=True):
            _handle_user_input("최근 뉴스 중 가장 주목할 만한 건 뭐야?")
    with col2:
        if st.button("💼 포트폴리오 점검", use_container_width=True):
            _handle_user_input("지금 보유 포트폴리오에 대한 의견을 줘")
        if st.button("🔬 반도체 섹터 분석", use_container_width=True):
            _handle_user_input("지금 반도체 섹터에 진입해도 될까?")


def _handle_user_input(prompt: str):
    # 사용자 메시지 추가
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 토픽 기반 추가 컨텍스트
    extra_context = st.session_state.context_builder.build_topic_context(prompt)

    # Gemini 스트리밍 응답
    with st.chat_message("assistant"):
        try:
            response_placeholder = st.empty()
            full_response = ""
            for chunk in st.session_state.chat.send_message_stream(prompt, extra_context):
                full_response += chunk
                response_placeholder.markdown(full_response + "▌")
            response_placeholder.markdown(full_response)
        except Exception as e:
            full_response = f"응답 생성 중 오류가 발생했습니다: {e}"
            st.error(full_response)

    # 어시스턴트 메시지 저장
    st.session_state.messages.append({"role": "assistant", "content": full_response})


if __name__ == "__main__":
    main()
