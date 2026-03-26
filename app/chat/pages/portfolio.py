"""포트폴리오 대시보드 — Streamlit 멀티페이지"""

import logging
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="포트폴리오 대시보드", page_icon="📊", layout="wide")

from app.chat.db_reader import DBReader

logger = logging.getLogger(__name__)


# ── 데이터 로드 ──


def _get_db() -> DBReader:
    if "db" not in st.session_state:
        st.session_state.db = DBReader()
    return st.session_state.db


def _load_portfolio() -> dict:
    """KIS API 잔고 조회 (db_reader.get_current_positions 재사용)"""
    db = _get_db()
    try:
        result = db.get_current_positions()
        logger.info(
            "포트폴리오 로드 (source=%s, %d개)",
            result.get("source", "?"),
            len(result.get("positions", [])),
        )
        return result
    except Exception as e:
        logger.error("포트폴리오 로드 실패: %s", e, exc_info=True)
        return {"positions": [], "cash": 0, "total_eval": 0, "eval_pnl": 0, "source": "error"}


def _load_trades() -> list[dict]:
    db = _get_db()
    try:
        return db.get_trade_history(days=7, limit=30)
    except Exception as e:
        logger.warning("거래이력 로드 실패: %s", e)
        return []


def _load_performance() -> list[dict]:
    db = _get_db()
    try:
        return db.get_daily_performance(days=30)
    except Exception as e:
        logger.warning("일일성과 로드 실패: %s", e)
        return []


def _load_regime() -> dict:
    db = _get_db()
    try:
        return db.get_latest_regime()
    except Exception:
        return {}


# ── 메인 렌더링 ──


def render():
    st.title("📊 포트폴리오 대시보드")

    # 새로고침 버튼
    col_title, col_refresh = st.columns([4, 1])
    with col_refresh:
        if st.button("🔄 새로고침", use_container_width=True):
            for k in ("_pf_data", "_pf_trades", "_pf_perf"):
                st.session_state.pop(k, None)
            st.rerun()

    # 데이터 로드 (세션 캐시)
    if "_pf_data" not in st.session_state:
        st.session_state._pf_data = _load_portfolio()
    if "_pf_trades" not in st.session_state:
        st.session_state._pf_trades = _load_trades()
    if "_pf_perf" not in st.session_state:
        st.session_state._pf_perf = _load_performance()

    pf = st.session_state._pf_data
    positions = pf.get("positions", [])
    cash = pf.get("cash", 0)
    total_eval = pf.get("total_eval", 0)
    eval_pnl = pf.get("eval_pnl", 0)
    purchase_amt = pf.get("purchase_amount", 0)
    source = pf.get("source", "")

    # ─────────────────────────────────────────────────
    # 상단: 계좌 요약
    # ─────────────────────────────────────────────────
    st.subheader("💰 계좌 요약")
    if source:
        st.caption(f"데이터 소스: {source}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 평가금액", f"{total_eval:,.0f}원")
    c2.metric("예수금 (현금)", f"{cash:,.0f}원")
    c3.metric(
        "평가 손익",
        f"{eval_pnl:+,.0f}원",
        delta=f"{eval_pnl / purchase_amt * 100:+.2f}%" if purchase_amt else None,
    )
    # 총 수익률
    if purchase_amt:
        total_return_pct = (total_eval + cash - purchase_amt - cash) / purchase_amt * 100 if purchase_amt else 0
        c4.metric("총 수익률", f"{total_return_pct:+.2f}%")
    else:
        invested = sum(
            p.get("avg_price", 0) * p.get("quantity", 0) for p in positions
        )
        total_return_pct = (eval_pnl / invested * 100) if invested else 0
        c4.metric("총 수익률", f"{total_return_pct:+.2f}%")

    st.divider()

    # ─────────────────────────────────────────────────
    # 중단: 보유 종목 테이블
    # ─────────────────────────────────────────────────
    st.subheader(f"📋 보유 종목 ({len(positions)}개)")

    if positions:
        rows = []
        for p in positions:
            pnl_pct = p.get("pnl_pct", 0)
            pnl_icon = "🟢" if pnl_pct >= 0 else "🔴"
            rows.append({
                "종목명": p.get("name", ""),
                "종목코드": p.get("ticker", ""),
                "보유수량": p.get("quantity", 0),
                "매입평균가": f'{p.get("avg_price", 0):,.0f}',
                "현재가": f'{p.get("current_price", 0):,.0f}',
                "평가금액": f'{p.get("eval_amount", 0):,.0f}',
                "손익금액": f'{p.get("pnl", 0):+,.0f}',
                "수익률": f'{pnl_icon} {pnl_pct:+.2f}%',
            })

        df_pos = pd.DataFrame(rows)
        st.dataframe(df_pos, use_container_width=True, hide_index=True)

        # 종목별 수익률 색상 표시
        st.markdown("##### 종목별 수익률")
        cols = st.columns(min(len(positions), 4))
        for i, p in enumerate(positions):
            pnl_pct = p.get("pnl_pct", 0)
            name = p.get("name", p.get("ticker", "?"))
            color = "red" if pnl_pct >= 0 else "blue"
            icon = "🟢" if pnl_pct >= 0 else "🔴"
            with cols[i % len(cols)]:
                st.markdown(
                    f"{icon} **{name}**<br>"
                    f'<span style="color:{color}; font-size:1.3em; font-weight:bold">'
                    f"{pnl_pct:+.2f}%</span>",
                    unsafe_allow_html=True,
                )
    else:
        st.info("현재 보유 종목이 없습니다.")

    st.divider()

    # ─────────────────────────────────────────────────
    # 하단: 자산 배분 현황
    # ─────────────────────────────────────────────────
    st.subheader("⚖️ 자산 배분 현황")

    # 현재 배분 계산 (종목별 eval_amount + 현금)
    alloc_map: dict[str, float] = {}
    asset_type_keywords = {
        "주식": ["주식", "코스피", "코스닥", "KODEX 200", "TIGER", "S&P", "나스닥"],
        "채권": ["채권", "국채", "bond", "국고채"],
        "금": ["금", "골드", "gold", "Gold"],
        "원자재": ["원유", "원자재", "commodity"],
    }
    for p in positions:
        name = (p.get("name", "") + " " + p.get("ticker", "")).lower()
        eval_amt = p.get("eval_amount", 0) or (
            p.get("current_price", 0) * p.get("quantity", 0)
        )
        classified = False
        for asset_type, keywords in asset_type_keywords.items():
            if any(kw.lower() in name for kw in keywords):
                alloc_map[asset_type] = alloc_map.get(asset_type, 0) + eval_amt
                classified = True
                break
        if not classified:
            alloc_map["기타"] = alloc_map.get("기타", 0) + eval_amt

    alloc_map["현금"] = cash

    total_asset = sum(alloc_map.values()) or 1  # avoid div/0

    col_pie, col_bar = st.columns(2)

    # 파이 차트: 현재 배분
    with col_pie:
        st.markdown("**현재 자산 배분**")
        if any(v > 0 for v in alloc_map.values()):
            labels = list(alloc_map.keys())
            values = list(alloc_map.values())
            colors = {
                "주식": "#ef4444", "채권": "#3b82f6", "금": "#f59e0b",
                "원자재": "#8b5cf6", "현금": "#6b7280", "기타": "#10b981",
            }
            fig_pie = go.Figure(data=[go.Pie(
                labels=labels,
                values=values,
                marker=dict(colors=[colors.get(l, "#94a3b8") for l in labels]),
                textinfo="label+percent",
                hole=0.4,
            )])
            fig_pie.update_layout(
                margin=dict(t=20, b=20, l=20, r=20),
                height=350,
                showlegend=False,
            )
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.caption("배분 데이터 없음")

    # 바 차트: 목표 vs 현재
    with col_bar:
        st.markdown("**목표 배분 vs 현재 배분**")
        regime = _load_regime()
        target_alloc = regime.get("asset_allocation_suggestion", {})
        if not target_alloc:
            target_alloc = {"주식": 40, "채권": 30, "금": 15, "현금": 15}

        categories = sorted(
            set(list(target_alloc.keys()) + list(alloc_map.keys())),
            key=lambda x: ["주식", "채권", "금", "원자재", "현금", "기타"].index(x)
            if x in ["주식", "채권", "금", "원자재", "현금", "기타"] else 99,
        )
        target_vals = [target_alloc.get(c, 0) for c in categories]
        current_vals = [round(alloc_map.get(c, 0) / total_asset * 100, 1) for c in categories]

        fig_bar = go.Figure(data=[
            go.Bar(name="목표", x=categories, y=target_vals, marker_color="#3b82f6"),
            go.Bar(name="현재", x=categories, y=current_vals, marker_color="#ef4444"),
        ])
        fig_bar.update_layout(
            barmode="group",
            yaxis_title="%",
            margin=dict(t=20, b=20, l=40, r=20),
            height=350,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    st.divider()

    # ─────────────────────────────────────────────────
    # 일일 성과 추이
    # ─────────────────────────────────────────────────
    perf = st.session_state._pf_perf
    if perf:
        st.subheader("📈 포트폴리오 성과 추이 (최근 30일)")
        df_perf = pd.DataFrame(perf)
        if "date" in df_perf.columns:
            df_perf["date"] = pd.to_datetime(df_perf["date"])
            df_perf = df_perf.sort_values("date")

            col_val, col_ret = st.columns(2)
            with col_val:
                if "portfolio_value" in df_perf.columns:
                    st.markdown("**포트폴리오 가치**")
                    st.line_chart(df_perf.set_index("date")["portfolio_value"])
            with col_ret:
                if "cumulative_return" in df_perf.columns:
                    st.markdown("**누적 수익률**")
                    st.line_chart(df_perf.set_index("date")["cumulative_return"])

            if "drawdown" in df_perf.columns:
                st.markdown("**Drawdown**")
                st.area_chart(df_perf.set_index("date")["drawdown"])

        st.divider()

    # ─────────────────────────────────────────────────
    # 맨 아래: 최근 거래 이력
    # ─────────────────────────────────────────────────
    st.subheader("📝 최근 거래 이력 (7일)")
    trades = st.session_state._pf_trades
    if trades:
        rows = []
        for t in trades:
            side = t.get("side", "")
            side_display = "🔴 매수" if side == "buy" else "🔵 매도"
            rows.append({
                "일자": str(t.get("date", ""))[:10],
                "매매": side_display,
                "종목코드": t.get("ticker", ""),
                "수량": t.get("quantity", 0),
                "가격": f'{t.get("price", 0):,.0f}',
                "금액": f'{t.get("amount", 0):,.0f}',
                "손익": f'{t.get("pnl", 0):+,.0f}' if t.get("pnl") else "-",
                "수익률": f'{t.get("pnl_pct", 0):+.2f}%' if t.get("pnl_pct") else "-",
                "사유": t.get("trigger", ""),
            })
        df_trades = pd.DataFrame(rows)
        st.dataframe(df_trades, use_container_width=True, hide_index=True)
    else:
        st.info("최근 7일간 거래 내역이 없습니다.")


render()
