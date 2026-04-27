import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import requests
import os
from datetime import date, datetime, timedelta

# ── 設定 ──────────────────────────────────────────────────────────────────────

def _get_secret(key: str, default: str = "") -> str:
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)

# ── 專案參數（所有欄位皆可透過 env var / secrets.toml 覆寫）──────────────────

CAMPAIGN_ID    = _get_secret("CAMPAIGN_ID",    "6939598565939")
AD_ACCOUNT_ID  = _get_secret("AD_ACCOUNT_ID",  "act_111854365566947")
PAGE_TITLE     = _get_secret("PAGE_TITLE",     "超老闆美業行銷課前測數據儀表板")
CAMPAIGN_LABEL = _get_secret("CAMPAIGN_LABEL", "【勿動】超老闆前測問卷_柏廷")

# SHEET_CSV_URLS：逗號分隔的多個 Google Sheet CSV export URL
_raw_urls = _get_secret(
    "SHEET_CSV_URLS",
    "https://docs.google.com/spreadsheets/d/1evy1dsWqotGtOjB7JHCuppi3pfy1oISoOjHeJnWfQ5o/export?format=csv&gid=2114940393,"
    "https://docs.google.com/spreadsheets/d/13dS5ILsNtnO6ZNqTaBhPNyiXTmjcalk2ZOLC_EjUtoU/export?format=csv&gid=1143313092"
)
SHEET_CSV_URLS = [u.strip() for u in _raw_urls.split(",") if u.strip()]

st.set_page_config(
    page_title=PAGE_TITLE,
    page_icon="📊",
    layout="wide",
)

def get_access_token() -> str:
    return _get_secret("META_ACCESS_TOKEN")

def get_app_id() -> str:
    return _get_secret("META_APP_ID")

def get_app_secret() -> str:
    return _get_secret("META_APP_SECRET")

def exchange_long_term_token(short_term_token: str) -> dict:
    """用短期 token 換取長期 token（60 天），回傳 {access_token, expires_in} 或 {error}"""
    resp = requests.get(
        "https://graph.facebook.com/v19.0/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": get_app_id(),
            "client_secret": get_app_secret(),
            "fb_exchange_token": short_term_token,
        },
    )
    return resp.json()

def inspect_token(token: str) -> dict:
    """查詢 token 的到期時間等資訊"""
    resp = requests.get(
        "https://graph.facebook.com/v19.0/debug_token",
        params={
            "input_token": token,
            "access_token": f"{get_app_id()}|{get_app_secret()}",
        },
    )
    return resp.json().get("data", {})


# ── 資料抓取 ──────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def fetch_meta_insights(start_date: str, end_date: str) -> pd.DataFrame:
    token = get_access_token()
    if not token:
        return pd.DataFrame()

    url = f"https://graph.facebook.com/v19.0/{CAMPAIGN_ID}/insights"
    params = {
        "fields": "spend,clicks,impressions,cpc",
        "time_range": f'{{"since":"{start_date}","until":"{end_date}"}}',
        "time_increment": 1,
        "limit": 500,
        "access_token": token,
    }
    rows = []
    while url:
        resp = requests.get(url, params=params if rows == [] else None)
        data = resp.json()
        if "error" in data:
            st.error(f"Meta API 錯誤：{data['error'].get('message', data['error'])}")
            break
        rows.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
        params = None  # next page URL already contains params

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date_start"])
    df["spend"] = df["spend"].astype(float)
    df["clicks"] = df["clicks"].astype(int)
    df["impressions"] = df["impressions"].astype(int)
    df["cpc"] = df["cpc"].astype(float)
    return df[["date", "spend", "clicks", "impressions", "cpc"]].sort_values("date")


def _fetch_csv(url: str, label: str) -> pd.DataFrame:
    try:
        import io
        resp = requests.get(url, verify=False, timeout=15)
        resp.raise_for_status()
        return pd.read_csv(io.BytesIO(resp.content), encoding="utf-8-sig")
    except Exception as e:
        st.warning(f"無法讀取 Google Sheet（{label}）：{e}")
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_sheet_data() -> pd.DataFrame:
    frames = []
    for i, url in enumerate(SHEET_CSV_URLS, 1):
        df = _fetch_csv(url, f"第 {i} 份")
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ── 工具函式 ──────────────────────────────────────────────────────────────────

def fmt_currency(val: float) -> str:
    return f"NT$ {val:,.0f}"

def fmt_number(val: float) -> str:
    return f"{val:,.0f}"

def parse_tw_datetime(series: pd.Series) -> pd.Series:
    """解析 '2026/3/22 上午 1:11:15' 這類中文 AM/PM 格式"""
    def _parse(val):
        if not isinstance(val, str):
            return pd.NaT
        val = val.replace("上午", "AM").replace("下午", "PM")
        try:
            return datetime.strptime(val, "%Y/%m/%d %p %I:%M:%S")
        except Exception:
            return pd.NaT
    return series.apply(_parse)

def detect_date_column(df: pd.DataFrame):
    """回傳 DataFrame 中最可能是日期/時間戳記的欄位名稱"""
    for col in df.columns:
        if any(kw in col for kw in ["時間", "日期", "Timestamp", "timestamp", "Submitted", "submitted", "date", "Date"]):
            return col
    return None


# ── 主畫面 ────────────────────────────────────────────────────────────────────

st.title(PAGE_TITLE)
st.caption(f"數據每 5 分鐘自動更新 · 行銷活動：{CAMPAIGN_LABEL}")

# 檢查 token
if not get_access_token():
    st.error("請設定環境變數 META_ACCESS_TOKEN")
    st.stop()

# ── 日期選擇 ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("篩選條件")

    # ── Token 狀態 ─────────────────────────────────────────────────────────
    with st.expander("Token 管理", expanded=False):
        token_info = inspect_token(get_access_token())
        if token_info:
            exp_ts = token_info.get("expires_at", 0)
            if exp_ts:
                exp_dt = datetime.fromtimestamp(exp_ts)
                days_left = (exp_dt - datetime.now()).days
                if days_left > 7:
                    st.success(f"Token 有效，剩餘 **{days_left} 天**（到期：{exp_dt.strftime('%Y-%m-%d')}）")
                elif days_left > 0:
                    st.warning(f"Token 即將到期，剩餘 **{days_left} 天**")
                else:
                    st.error("Token 已過期")
            else:
                st.info("Token 無限期（永久 token）")

        st.caption("貼上短期 token 以換取新的長期 token（60 天）")
        new_short_token = st.text_area("短期 Token", height=100, placeholder="貼上從 Meta Business Manager 複製的短期 token", label_visibility="collapsed")
        if st.button("換取長期 Token", use_container_width=True):
            if new_short_token.strip():
                with st.spinner("交換中..."):
                    result = exchange_long_term_token(new_short_token.strip())
                if "access_token" in result:
                    exp_days = result.get("expires_in", 0) // 86400
                    st.success(f"換取成功！有效期 {exp_days} 天")
                    st.code(result["access_token"], language=None)
                    st.caption("複製上方 token，更新到 Zeabur 環境變數 META_ACCESS_TOKEN")
                else:
                    err = result.get("error", {})
                    st.error(f"失敗：{err.get('message', result)}")
            else:
                st.warning("請貼上短期 token")

    st.divider()

    # 先抓全期資料以取得可用日期範圍（Meta API 最多支援往前 37 個月）
    earliest = date.today() - timedelta(days=37 * 30)
    with st.spinner("載入日期範圍..."):
        full_df = fetch_meta_insights(earliest.strftime("%Y-%m-%d"), date.today().strftime("%Y-%m-%d"))

    if full_df.empty:
        st.stop()

    min_date = full_df["date"].min().date()
    max_date = date.today()

    date_range = st.date_input(
        "日期範圍",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start, end = date_range
    else:
        start = end = date_range

    st.divider()
    if st.button("重新整理資料", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ── 依日期範圍過濾 ─────────────────────────────────────────────────────────────

start_str = start.strftime("%Y-%m-%d")
end_str   = end.strftime("%Y-%m-%d")

with st.spinner("載入廣告數據..."):
    meta_df = fetch_meta_insights(start_str, end_str)

with st.spinner("載入名單數據..."):
    sheet_df = fetch_sheet_data()

# ── 名單數量 & 日期分布 ────────────────────────────────────────────────────────

date_col = detect_date_column(sheet_df) if not sheet_df.empty else None

if date_col and not sheet_df.empty:
    sheet_df[date_col] = parse_tw_datetime(sheet_df[date_col])
    filtered_sheet = sheet_df[
        (sheet_df[date_col] >= pd.Timestamp(start)) &
        (sheet_df[date_col] <= pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
    ]
    # 每日名單數
    daily_leads = (
        filtered_sheet.groupby(filtered_sheet[date_col].dt.normalize())
        .size()
        .reset_index(name="leads")
    )
    daily_leads = daily_leads.rename(columns={date_col: "date"})
    daily_leads = daily_leads[["date", "leads"]]
    total_leads = int(filtered_sheet.shape[0])
else:
    # 無時間欄位 → 只顯示總數
    total_leads = int(sheet_df.shape[0]) if not sheet_df.empty else 0
    daily_leads = pd.DataFrame()

# ── 彙總指標 ──────────────────────────────────────────────────────────────────

total_spend      = meta_df["spend"].sum() if not meta_df.empty else 0.0
total_clicks     = int(meta_df["clicks"].sum()) if not meta_df.empty else 0
total_impressions = int(meta_df["impressions"].sum()) if not meta_df.empty else 0
avg_cpc          = (total_spend / total_clicks) if total_clicks > 0 else 0.0
cost_per_lead    = (total_spend / total_leads) if total_leads > 0 else 0.0

# ── KPI 卡片 ──────────────────────────────────────────────────────────────────

st.subheader("整體成效")
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("總花費",       fmt_currency(total_spend))
c2.metric("總點擊數",     fmt_number(total_clicks))
c3.metric("總曝光數",     fmt_number(total_impressions))
c4.metric("平均 CPC",     f"NT$ {avg_cpc:.2f}")
c5.metric("前測名單數",   fmt_number(total_leads))
c6.metric("名單成本",     fmt_currency(cost_per_lead))

st.divider()

# ── 趨勢圖 ────────────────────────────────────────────────────────────────────

st.subheader("數據趨勢")

if not meta_df.empty:
    # 合併每日名單
    chart_df = meta_df.copy()
    if not daily_leads.empty:
        chart_df = chart_df.merge(daily_leads, on="date", how="left")
        chart_df["leads"] = chart_df["leads"].fillna(0).astype(int)
        # 累積成本 = 累積花費 / 累積名單
        chart_df["cum_spend"] = chart_df["spend"].cumsum()
        chart_df["cum_leads"] = chart_df["leads"].cumsum()
        chart_df["daily_cost_per_lead"] = chart_df.apply(
            lambda r: r["cum_spend"] / r["cum_leads"] if r["cum_leads"] > 0 else None,
            axis=1,
        )
    else:
        chart_df["daily_cost_per_lead"] = None

    fig = go.Figure()

    # 長條圖：每日花費
    fig.add_trace(go.Bar(
        x=chart_df["date"],
        y=chart_df["spend"],
        name="每日花費 (NT$)",
        marker_color="#4C9BE8",
        yaxis="y1",
        hovertemplate="%{x|%Y-%m-%d}<br>花費：NT$ %{y:,.0f}<extra></extra>",
    ))

    # 折線圖：累積名單成本
    if chart_df["daily_cost_per_lead"].notna().any():
        fig.add_trace(go.Scatter(
            x=chart_df["date"],
            y=chart_df["daily_cost_per_lead"],
            name="累積名單成本 (NT$)",
            mode="lines+markers",
            line=dict(color="#FF6B6B", width=2),
            marker=dict(size=6),
            yaxis="y2",
            hovertemplate="%{x|%Y-%m-%d}<br>名單成本：NT$ %{y:,.0f}<extra></extra>",
        ))

    fig.update_layout(
        xaxis=dict(title="日期", tickformat="%m/%d"),
        yaxis=dict(title="每日花費 (NT$)", showgrid=False),
        yaxis2=dict(
            title="累積名單成本 (NT$)",
            overlaying="y",
            side="right",
            showgrid=False,
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
        height=420,
        margin=dict(l=0, r=0, t=10, b=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("所選日期範圍內無廣告數據")

st.divider()

# ── 每日數據列表 ───────────────────────────────────────────────────────────────

st.subheader("每日數據")

if not meta_df.empty:
    table_df = meta_df.copy()
    if not daily_leads.empty:
        table_df = table_df.merge(daily_leads, on="date", how="left")
        table_df["leads"] = table_df["leads"].fillna(0).astype(int)
        table_df["cost_per_lead"] = table_df.apply(
            lambda r: r["spend"] / r["leads"] if r["leads"] > 0 else None, axis=1
        )
    else:
        table_df["leads"] = "-"
        table_df["cost_per_lead"] = "-"

    table_df["date"] = table_df["date"].dt.strftime("%Y-%m-%d")

    # 合計列
    totals = {
        "date": "合計",
        "spend": total_spend,
        "clicks": total_clicks,
        "impressions": total_impressions,
        "cpc": avg_cpc,
        "leads": total_leads if total_leads > 0 else "-",
        "cost_per_lead": cost_per_lead if total_leads > 0 else "-",
    }
    table_df = pd.concat(
        [table_df, pd.DataFrame([totals])],
        ignore_index=True,
    )

    # 欄位格式化（數字欄位）
    def fmt_col(df, col, fmt_fn):
        if col in df.columns:
            df[col] = df[col].apply(
                lambda v: fmt_fn(v) if isinstance(v, (int, float)) else v
            )

    display_df = table_df.copy()
    fmt_col(display_df, "spend",         lambda v: f"NT$ {v:,.0f}")
    fmt_col(display_df, "clicks",        lambda v: f"{v:,}")
    fmt_col(display_df, "impressions",   lambda v: f"{v:,}")
    fmt_col(display_df, "cpc",           lambda v: f"NT$ {v:.2f}")
    fmt_col(display_df, "leads",         lambda v: f"{v:,}")
    fmt_col(display_df, "cost_per_lead", lambda v: f"NT$ {v:,.0f}")

    display_df.columns = ["日期", "花費", "點擊數", "曝光數", "CPC", "名單數", "名單成本"]

    st.dataframe(display_df, use_container_width=True, hide_index=True)
else:
    st.info("所選日期範圍內無廣告數據")
