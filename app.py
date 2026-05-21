import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import requests
import os
from datetime import date, datetime, timedelta, timezone

# 台灣時區（伺服器可能跑在 UTC，需要明確指定才能拿到正確的「今天」）
TW_TZ = timezone(timedelta(hours=8))

def tw_today() -> date:
    return datetime.now(TW_TZ).date()

# ── 設定 ──────────────────────────────────────────────────────────────────────

def _get_secret(key: str, default: str = "") -> str:
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)

# ── 專案參數（所有欄位皆可透過 env var / secrets.toml 覆寫）──────────────────

CAMPAIGN_ID    = _get_secret("CAMPAIGN_ID",    "6939598565939")
AD_ACCOUNT_ID  = _get_secret("AD_ACCOUNT_ID",  "act_111854365566947")
PAGE_TITLE     = _get_secret("PAGE_TITLE",     "超老闆美業行銷課數據儀表板")
CAMPAIGN_LABEL = _get_secret("CAMPAIGN_LABEL", "【勿動】超老闆前測問卷_柏廷")

# 前測期專用的 Meta IDs（不設則 fallback 用上面的，方便單一活動的舊報表沿用）
PRETEST_CAMPAIGN_ID   = _get_secret("PRETEST_CAMPAIGN_ID",   CAMPAIGN_ID)
PRETEST_AD_ACCOUNT_ID = _get_secret("PRETEST_AD_ACCOUNT_ID", AD_ACCOUNT_ID)

# 銷售期起始日（此日起，主畫面顯示銷售報表，之前的前測數據收進摺疊區塊）
SALES_START_DATE = _get_secret("SALES_START_DATE", "2026-05-15")

# Teachify Admin API
TEACHIFY_API_KEY = _get_secret("TEACHIFY_API_KEY", "")
TEACHIFY_GRAPHQL = "https://teachify.io/admin/graphql"

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


# ── Meta 廣告 ─────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def fetch_meta_insights(start_date: str, end_date: str, campaign_id: str = "") -> pd.DataFrame:
    token = get_access_token()
    if not token:
        return pd.DataFrame()

    cid = campaign_id or CAMPAIGN_ID
    url = f"https://graph.facebook.com/v19.0/{cid}/insights"
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


@st.cache_data(ttl=300, show_spinner=False)
def fetch_meta_ad_insights(start_date: str, end_date: str, campaign_id: str = "") -> pd.DataFrame:
    """以 ad-level 抓素材成效（廣告組合、廣告、CTR、CPC、CPM、轉換等）。"""
    token = get_access_token()
    if not token:
        return pd.DataFrame()

    cid = campaign_id or CAMPAIGN_ID
    url = f"https://graph.facebook.com/v19.0/{cid}/insights"
    params = {
        "level": "ad",
        "fields": "ad_id,ad_name,adset_id,adset_name,spend,impressions,clicks,ctr,cpc,cpm,reach,frequency,actions,cost_per_action_type",
        "time_range": f'{{"since":"{start_date}","until":"{end_date}"}}',
        "limit": 500,
        "access_token": token,
    }
    rows = []
    while url:
        resp = requests.get(url, params=params if rows == [] else None)
        data = resp.json()
        if "error" in data:
            st.error(f"Meta API 錯誤（素材成效）：{data['error'].get('message', data['error'])}")
            break
        rows.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
        params = None

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    for col in ["spend", "ctr", "cpc", "cpm", "frequency"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    for col in ["impressions", "clicks", "reach"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    # 從 actions 中萃取「購買」轉換數與「加入購物車」（依需要可擴充）
    def _extract_action(actions, target_types):
        if not isinstance(actions, list):
            return 0
        return sum(int(float(a.get("value", 0))) for a in actions if a.get("action_type") in target_types)

    purchase_types = ["purchase", "offsite_conversion.fb_pixel_purchase",
                      "omni_purchase", "onsite_web_purchase"]
    lead_types     = ["lead", "offsite_conversion.fb_pixel_lead",
                      "onsite_conversion.lead_grouped"]
    landing_types  = ["landing_page_view"]

    df["purchases"]      = df.get("actions", pd.Series([None] * len(df))).apply(lambda a: _extract_action(a, purchase_types))
    df["leads_action"]   = df.get("actions", pd.Series([None] * len(df))).apply(lambda a: _extract_action(a, lead_types))
    df["lp_views"]       = df.get("actions", pd.Series([None] * len(df))).apply(lambda a: _extract_action(a, landing_types))

    # 計算 CPA（每筆轉換成本）— 優先用購買數，沒有就用名單數
    def _cpa(row):
        n = row["purchases"] if row["purchases"] > 0 else row["leads_action"]
        return row["spend"] / n if n > 0 else None
    df["cpa"] = df.apply(_cpa, axis=1)

    cols_keep = ["ad_id", "ad_name", "adset_id", "adset_name", "spend",
                 "impressions", "clicks", "ctr", "cpc", "cpm", "reach", "frequency",
                 "purchases", "leads_action", "lp_views", "cpa"]
    cols_keep = [c for c in cols_keep if c in df.columns]
    return df[cols_keep].sort_values("spend", ascending=False)


# ── Google Sheet（前測期名單）────────────────────────────────────────────────

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


# ── Teachify Admin API ────────────────────────────────────────────────────────

def _teachify_query(query: str, variables: dict | None = None) -> dict:
    if not TEACHIFY_API_KEY:
        return {"errors": [{"message": "尚未設定 TEACHIFY_API_KEY"}]}
    resp = requests.post(
        TEACHIFY_GRAPHQL,
        headers={
            "X-Teachify-API-Key": TEACHIFY_API_KEY,
            "Content-Type": "application/json",
        },
        json={"query": query, "variables": variables or {}},
        timeout=30,
    )
    return resp.json()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_teachify_payments(start_ts: int, end_ts: int) -> pd.DataFrame:
    """抓 [start_ts, end_ts] 期間已付款的訂單。回傳 DataFrame 含日期、金額、折扣碼。"""
    query = """
    query($filter: AdminPaymentFilter, $page: Int!, $perPage: Int!) {
      payments(filter: $filter, page: $page, perPage: $perPage) {
        nodesCount
        totalPages
        hasNextPage
        nodes {
          id
          amount
          discountAmount
          couponCode
          paidAt
          refundedAt
          tradeNo
          lineitems { name amount }
        }
      }
    }
    """
    all_rows: list[dict] = []
    page = 1
    while True:
        variables = {
            "filter": {"paidAt": {"gte": start_ts, "lte": end_ts}},
            "page": page,
            "perPage": 50,
        }
        data = _teachify_query(query, variables)
        if "errors" in data:
            st.error(f"Teachify API 錯誤：{data['errors'][0].get('message')}")
            return pd.DataFrame()
        payload = data.get("data", {}).get("payments", {})
        all_rows.extend(payload.get("nodes", []))
        if not payload.get("hasNextPage"):
            break
        page += 1

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["paidAt_dt"] = pd.to_datetime(df["paidAt"], unit="s")
    df["date"] = df["paidAt_dt"].dt.normalize()
    df["amount"] = df["amount"].astype(float)
    df["discountAmount"] = df["discountAmount"].fillna(0).astype(float)
    df["used_coupon"] = df["couponCode"].notna() & (df["couponCode"] != "")
    return df


@st.cache_data(ttl=300, show_spinner=False)
def fetch_teachify_coupons() -> pd.DataFrame:
    """抓所有折扣碼及其累積使用次數。"""
    query = """
    query($page: Int!, $perPage: Int!) {
      coupons(page: $page, perPage: $perPage) {
        nodesCount
        hasNextPage
        nodes {
          id code name amount couponType appliedCount redemptionLimit active
        }
      }
    }
    """
    all_rows: list[dict] = []
    page = 1
    while True:
        data = _teachify_query(query, {"page": page, "perPage": 50})
        if "errors" in data:
            st.error(f"Teachify Coupons API 錯誤：{data['errors'][0].get('message')}")
            return pd.DataFrame()
        payload = data.get("data", {}).get("coupons", {})
        all_rows.extend(payload.get("nodes", []))
        if not payload.get("hasNextPage"):
            break
        page += 1

    if not all_rows:
        return pd.DataFrame()
    df = pd.DataFrame(all_rows)
    return df


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
st.caption("數據每 5 分鐘自動更新")

# 檢查 token
if not get_access_token():
    st.error("請設定環境變數 META_ACCESS_TOKEN")
    st.stop()

try:
    sales_start = datetime.strptime(SALES_START_DATE, "%Y-%m-%d").date()
except Exception:
    st.error(f"SALES_START_DATE 格式錯誤（應為 YYYY-MM-DD），目前值：{SALES_START_DATE}")
    st.stop()

# ── 側邊欄 ────────────────────────────────────────────────────────────────────

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

    # 先抓全期 Meta 資料以取得可用日期範圍
    earliest = tw_today() - timedelta(days=37 * 30)
    today = tw_today()
    yesterday = today - timedelta(days=1)
    with st.spinner("載入日期範圍..."):
        full_df = fetch_meta_insights(earliest.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"))

    # 銷售期日期範圍：可選任何日期（含今天），預設從 sales_start 到今天
    sales_min_pickable = full_df["date"].min().date() if not full_df.empty else (sales_start - timedelta(days=30))
    sales_default_start = max(sales_start, sales_min_pickable) if sales_start <= today else sales_min_pickable

    date_range = st.date_input(
        "銷售期日期範圍",
        value=(sales_default_start, today),
        min_value=sales_min_pickable,
        max_value=today,
    )

    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start, end = date_range
    else:
        start = end = date_range

    st.divider()
    if st.button("重新整理資料", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    # ── 管理員設定 ─────────────────────────────────────────────────────────
    st.divider()

    ADMIN_PASSWORD = "wecan90202317"

    if "admin_unlocked" not in st.session_state:
        st.session_state["admin_unlocked"] = False
    if "page_mode" not in st.session_state:
        st.session_state["page_mode"] = "main"

    with st.expander("🔐 管理員設定", expanded=False):
        if not st.session_state["admin_unlocked"]:
            with st.form("admin_login", clear_on_submit=False):
                pw = st.text_input("密碼", type="password", placeholder="輸入管理員密碼", label_visibility="collapsed")
                if st.form_submit_button("登入", use_container_width=True):
                    if pw == ADMIN_PASSWORD:
                        st.session_state["admin_unlocked"] = True
                        st.session_state["page_mode"] = "admin"
                        st.rerun()
                    else:
                        st.error("密碼錯誤")
        else:
            st.success("已登入")
            page_choice = st.radio(
                "頁面切換",
                options=["主頁", "管理員（素材成效）"],
                index=0 if st.session_state["page_mode"] == "main" else 1,
                label_visibility="collapsed",
            )
            new_mode = "main" if page_choice == "主頁" else "admin"
            if new_mode != st.session_state["page_mode"]:
                st.session_state["page_mode"] = new_mode
                st.rerun()
            if st.button("🔒 登出", use_container_width=True):
                st.session_state["admin_unlocked"] = False
                st.session_state["page_mode"] = "main"
                st.rerun()

# ── 共用變數 ──────────────────────────────────────────────────────────────────

start_str = start.strftime("%Y-%m-%d")
end_str   = end.strftime("%Y-%m-%d")

# ── 頁面路由 ──────────────────────────────────────────────────────────────────

PAGE_MODE = st.session_state.get("page_mode", "main")
if PAGE_MODE == "admin" and not st.session_state.get("admin_unlocked"):
    # 防呆：未登入卻是 admin mode → 自動回主頁
    PAGE_MODE = "main"
    st.session_state["page_mode"] = "main"

# ── 管理員頁面（素材成效）────────────────────────────────────────────────────

if PAGE_MODE == "admin":
    st.header("🎨 素材成效（管理員）")
    st.caption(f"日期範圍：{start_str} ~ {end_str}（在側邊欄調整）")

    with st.spinner("載入素材成效..."):
        ad_df = fetch_meta_ad_insights(start_str, end_str)

    if ad_df.empty:
        st.info("所選日期範圍內無素材數據")
    else:
        def _safe_apply(df, col, fn):
            if col in df.columns:
                df[col] = df[col].apply(fn)

        # 廣告組合彙總
        st.subheader("廣告組合（Ad Set）彙總")
        if "adset_name" in ad_df.columns:
            agg_dict = {
                "spend":       ("spend", "sum"),
                "impressions": ("impressions", "sum"),
                "clicks":      ("clicks", "sum"),
                "purchases":   ("purchases", "sum"),
                "leads":       ("leads_action", "sum"),
                "lp_views":    ("lp_views", "sum"),
            }
            if "reach" in ad_df.columns:
                agg_dict["reach"] = ("reach", "sum")
            adset_agg = ad_df.groupby(["adset_id", "adset_name"], as_index=False).agg(**agg_dict)

            adset_agg["ctr"] = adset_agg.apply(
                lambda r: (r["clicks"] / r["impressions"] * 100) if r["impressions"] > 0 else 0, axis=1
            )
            adset_agg["cpc"] = adset_agg.apply(
                lambda r: r["spend"] / r["clicks"] if r["clicks"] > 0 else None, axis=1
            )
            adset_agg["cpa"] = adset_agg.apply(
                lambda r: r["spend"] / (r["purchases"] if r["purchases"] > 0 else r["leads"])
                          if (r["purchases"] > 0 or r["leads"] > 0) else None,
                axis=1,
            )

            adset_disp = adset_agg.copy()
            _safe_apply(adset_disp, "spend",       lambda v: f"NT$ {v:,.0f}")
            _safe_apply(adset_disp, "impressions", lambda v: f"{int(v):,}")
            _safe_apply(adset_disp, "clicks",      lambda v: f"{int(v):,}")
            _safe_apply(adset_disp, "reach",       lambda v: f"{int(v):,}")
            _safe_apply(adset_disp, "ctr",         lambda v: f"{v:.2f}%")
            _safe_apply(adset_disp, "cpc",         lambda v: f"NT$ {v:.2f}" if pd.notna(v) else "-")
            _safe_apply(adset_disp, "cpa",         lambda v: f"NT$ {v:,.0f}" if pd.notna(v) else "-")
            _safe_apply(adset_disp, "purchases",   lambda v: int(v))
            _safe_apply(adset_disp, "leads",       lambda v: int(v))
            _safe_apply(adset_disp, "lp_views",    lambda v: int(v))

            adset_cols_map = {
                "adset_name": "廣告組合", "spend": "花費", "impressions": "曝光",
                "reach": "觸及", "clicks": "點擊", "ctr": "CTR", "cpc": "CPC",
                "lp_views": "到達頁瀏覽", "leads": "名單", "purchases": "購買", "cpa": "CPA",
            }
            adset_show = [c for c in adset_cols_map if c in adset_disp.columns]
            adset_disp = adset_disp[adset_show]
            adset_disp.columns = [adset_cols_map[c] for c in adset_show]
            st.dataframe(adset_disp, use_container_width=True, hide_index=True)

        # 個別廣告（素材）明細
        st.subheader("個別廣告 / 素材明細")
        ad_disp = ad_df.copy()
        _safe_apply(ad_disp, "ctr",          lambda v: f"{v:.2f}%")
        _safe_apply(ad_disp, "spend",        lambda v: f"NT$ {v:,.0f}")
        _safe_apply(ad_disp, "impressions",  lambda v: f"{int(v):,}")
        _safe_apply(ad_disp, "clicks",       lambda v: f"{int(v):,}")
        _safe_apply(ad_disp, "reach",        lambda v: f"{int(v):,}")
        _safe_apply(ad_disp, "frequency",    lambda v: f"{v:.2f}")
        _safe_apply(ad_disp, "cpc",          lambda v: f"NT$ {v:.2f}" if v > 0 else "-")
        _safe_apply(ad_disp, "cpm",          lambda v: f"NT$ {v:.0f}")
        _safe_apply(ad_disp, "cpa",          lambda v: f"NT$ {v:,.0f}" if pd.notna(v) else "-")
        _safe_apply(ad_disp, "purchases",    int)
        _safe_apply(ad_disp, "leads_action", int)
        _safe_apply(ad_disp, "lp_views",     int)

        col_label_map = {
            "adset_name": "廣告組合", "ad_name": "廣告（素材）",
            "spend": "花費", "impressions": "曝光", "reach": "觸及",
            "frequency": "頻率", "clicks": "點擊", "ctr": "CTR",
            "cpc": "CPC", "cpm": "CPM", "lp_views": "到達頁瀏覽",
            "leads_action": "名單", "purchases": "購買", "cpa": "CPA",
        }
        show_cols = [c for c in col_label_map.keys() if c in ad_disp.columns]
        ad_disp = ad_disp[show_cols]
        ad_disp.columns = [col_label_map[c] for c in show_cols]
        st.dataframe(ad_disp, use_container_width=True, hide_index=True)

    st.stop()

# ── 銷售期數據 ────────────────────────────────────────────────────────────────

st.header("📈 銷售期數據")

with st.spinner("載入廣告數據..."):
    meta_df = fetch_meta_insights(start_str, end_str)

# Teachify 訂單（依日期過濾）
start_ts = int(datetime.combine(start, datetime.min.time()).timestamp())
end_ts   = int(datetime.combine(end, datetime.max.time()).timestamp())

if not TEACHIFY_API_KEY:
    st.warning("⚠️ 尚未設定 `TEACHIFY_API_KEY` 環境變數，銷售資料無法載入。請到 Zeabur Variables 設定。")
    payments_df = pd.DataFrame()
    coupons_df = pd.DataFrame()
else:
    with st.spinner("載入銷售數據..."):
        payments_df = fetch_teachify_payments(start_ts, end_ts)
    with st.spinner("載入折扣碼數據..."):
        coupons_df = fetch_teachify_coupons()

# 計算 KPI
total_spend       = meta_df["spend"].sum() if not meta_df.empty else 0.0
total_clicks      = int(meta_df["clicks"].sum()) if not meta_df.empty else 0
total_impressions = int(meta_df["impressions"].sum()) if not meta_df.empty else 0
avg_cpc           = (total_spend / total_clicks) if total_clicks > 0 else 0.0

total_orders   = int(payments_df.shape[0]) if not payments_df.empty else 0
total_revenue  = float(payments_df["amount"].sum()) if not payments_df.empty else 0.0
coupon_orders  = int(payments_df["used_coupon"].sum()) if not payments_df.empty else 0
cost_per_order = (total_spend / total_orders) if total_orders > 0 else 0.0
roas           = (total_revenue / total_spend) if total_spend > 0 else 0.0

# KPI 卡片
c1, c2, c3, c4 = st.columns(4)
c1.metric("銷售組數",       fmt_number(total_orders))
c2.metric("銷售金額",       fmt_currency(total_revenue))
c3.metric("折扣碼使用組數", fmt_number(coupon_orders))
c4.metric("ROAS",          f"{roas:.2f}x" if total_spend > 0 else "-")

c5, c6, c7, c8 = st.columns(4)
c5.metric("廣告花費",       fmt_currency(total_spend))
c6.metric("點擊數",         fmt_number(total_clicks))
c7.metric("平均 CPC",       f"NT$ {avg_cpc:.2f}")
c8.metric("每筆訂單成本",   fmt_currency(cost_per_order) if total_orders > 0 else "-")

st.divider()

# ── 銷售趨勢圖 ────────────────────────────────────────────────────────────────

st.subheader("每日銷售與廣告花費")

if not meta_df.empty:
    # 每日銷售統計
    if not payments_df.empty:
        daily_sales = (
            payments_df.groupby("date")
            .agg(orders=("id", "count"), revenue=("amount", "sum"))
            .reset_index()
        )
    else:
        daily_sales = pd.DataFrame(columns=["date", "orders", "revenue"])

    chart_df = meta_df.copy()
    chart_df = chart_df.merge(daily_sales, on="date", how="left")
    chart_df["orders"]  = chart_df["orders"].fillna(0).astype(int)
    chart_df["revenue"] = chart_df["revenue"].fillna(0).astype(float)

    # 依 SALES_START_DATE 將廣告花費拆成「名單期花費」與「銷售期花費」
    sales_start_ts = pd.Timestamp(sales_start)
    chart_df["spend_pretest"] = chart_df.apply(
        lambda r: r["spend"] if r["date"] < sales_start_ts else 0, axis=1
    )
    chart_df["spend_sales"] = chart_df.apply(
        lambda r: r["spend"] if r["date"] >= sales_start_ts else 0, axis=1
    )

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=chart_df["date"], y=chart_df["spend_pretest"],
        name="名單期廣告花費 (NT$)", marker_color="#9CA3AF", yaxis="y1",
        hovertemplate="%{x|%Y-%m-%d}<br>名單花費：NT$ %{y:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=chart_df["date"], y=chart_df["spend_sales"],
        name="銷售期廣告花費 (NT$)", marker_color="#4C9BE8", yaxis="y1",
        hovertemplate="%{x|%Y-%m-%d}<br>銷售花費：NT$ %{y:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=chart_df["date"], y=chart_df["revenue"],
        name="每日銷售金額 (NT$)", marker_color="#FFB454", yaxis="y1",
        hovertemplate="%{x|%Y-%m-%d}<br>銷售：NT$ %{y:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=chart_df["date"], y=chart_df["orders"],
        name="每日銷售組數", mode="lines+markers",
        line=dict(color="#FF6B6B", width=2), marker=dict(size=6), yaxis="y2",
        hovertemplate="%{x|%Y-%m-%d}<br>訂單：%{y}<extra></extra>",
    ))
    fig.update_layout(
        barmode="group",
        xaxis=dict(title="日期", tickformat="%m/%d"),
        yaxis=dict(title="金額 (NT$)", showgrid=False),
        yaxis2=dict(title="訂單數", overlaying="y", side="right", showgrid=False),
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

# ── 折扣碼明細 ────────────────────────────────────────────────────────────────

st.subheader("折扣碼使用狀況")

if not coupons_df.empty:
    display_coupons = coupons_df.copy()
    display_coupons["amount"] = display_coupons.apply(
        lambda r: f"{int(r['amount'])}%" if r["couponType"] == "percentage" else f"NT$ {int(r['amount']):,}",
        axis=1,
    )
    display_coupons["redemptionLimit"] = display_coupons["redemptionLimit"].apply(
        lambda v: f"{int(v)}" if pd.notna(v) else "無限制"
    )
    display_coupons["active"] = display_coupons["active"].map({True: "✅ 啟用", False: "❌ 停用"})
    display_coupons = display_coupons[["code", "name", "amount", "appliedCount", "redemptionLimit", "active"]]
    display_coupons.columns = ["折扣碼", "名稱", "折抵", "使用次數", "使用上限", "狀態"]
    display_coupons = display_coupons.sort_values("使用次數", ascending=False)
    st.dataframe(display_coupons, use_container_width=True, hide_index=True)
else:
    st.info("尚無折扣碼資料（或 Teachify API Key 未設定）")

st.divider()

# ── 銷售期每日明細 ────────────────────────────────────────────────────────────

st.subheader("每日銷售明細")

if not meta_df.empty:
    table_df = meta_df.copy()
    if not payments_df.empty:
        daily_sales = (
            payments_df.groupby("date")
            .agg(orders=("id", "count"), revenue=("amount", "sum"), coupon_used=("used_coupon", "sum"))
            .reset_index()
        )
        table_df = table_df.merge(daily_sales, on="date", how="left")
    else:
        table_df["orders"] = 0
        table_df["revenue"] = 0.0
        table_df["coupon_used"] = 0

    table_df["orders"]      = table_df["orders"].fillna(0).astype(int)
    table_df["revenue"]     = table_df["revenue"].fillna(0).astype(float)
    table_df["coupon_used"] = table_df["coupon_used"].fillna(0).astype(int)
    table_df["cost_per_order"] = table_df.apply(
        lambda r: r["spend"] / r["orders"] if r["orders"] > 0 else None, axis=1
    )
    table_df["date_str"] = table_df["date"].dt.strftime("%Y-%m-%d")
    table_df["phase"] = table_df["date"].apply(
        lambda d: "銷售期" if d >= pd.Timestamp(sales_start) else "名單期"
    )

    totals = {
        "date_str": "合計",
        "phase": "—",
        "spend": total_spend,
        "clicks": total_clicks,
        "impressions": total_impressions,
        "cpc": avg_cpc,
        "orders": total_orders,
        "revenue": total_revenue,
        "coupon_used": coupon_orders,
        "cost_per_order": cost_per_order if total_orders > 0 else None,
    }
    display = pd.concat(
        [table_df[["date_str", "phase", "spend", "clicks", "impressions", "cpc",
                   "orders", "revenue", "coupon_used", "cost_per_order"]],
         pd.DataFrame([totals])],
        ignore_index=True,
    )

    def _fmt(v, fn):
        return fn(v) if isinstance(v, (int, float)) and pd.notna(v) else "-"

    display["spend"]          = display["spend"].apply(lambda v: _fmt(v, lambda x: f"NT$ {x:,.0f}"))
    display["clicks"]         = display["clicks"].apply(lambda v: _fmt(v, lambda x: f"{int(x):,}"))
    display["impressions"]    = display["impressions"].apply(lambda v: _fmt(v, lambda x: f"{int(x):,}"))
    display["cpc"]            = display["cpc"].apply(lambda v: _fmt(v, lambda x: f"NT$ {x:.2f}"))
    display["orders"]         = display["orders"].apply(lambda v: _fmt(v, lambda x: f"{int(x):,}"))
    display["revenue"]        = display["revenue"].apply(lambda v: _fmt(v, lambda x: f"NT$ {x:,.0f}"))
    display["coupon_used"]    = display["coupon_used"].apply(lambda v: _fmt(v, lambda x: f"{int(x):,}"))
    display["cost_per_order"] = display["cost_per_order"].apply(lambda v: _fmt(v, lambda x: f"NT$ {x:,.0f}"))

    display.columns = ["日期", "階段", "廣告花費", "點擊", "曝光", "CPC",
                       "銷售組數", "銷售金額", "折扣碼使用", "每筆訂單成本"]
    st.dataframe(display, use_container_width=True, hide_index=True)
else:
    st.info("所選日期範圍內無廣告數據")

# ── 前測期數據（摺疊區塊）─────────────────────────────────────────────────────

st.divider()

with st.expander(f"📂 前測期數據（{SALES_START_DATE} 前）— 點擊展開", expanded=False):
    # 前測期：抓舊 campaign 的完整日期範圍以供選擇器使用
    pretest_end = sales_start - timedelta(days=1)
    _pretest_earliest = pretest_end - timedelta(days=37 * 30)
    _pretest_full = fetch_meta_insights(
        _pretest_earliest.strftime("%Y-%m-%d"),
        pretest_end.strftime("%Y-%m-%d"),
        campaign_id=PRETEST_CAMPAIGN_ID,
    )
    pretest_start = _pretest_full["date"].min().date() if not _pretest_full.empty else (pretest_end - timedelta(days=30))

    pre_date_range = st.date_input(
        "前測期日期範圍",
        value=(pretest_start, pretest_end),
        min_value=pretest_start,
        max_value=pretest_end,
        key="pretest_date_range",
    )
    if isinstance(pre_date_range, (list, tuple)) and len(pre_date_range) == 2:
        pre_start, pre_end = pre_date_range
    else:
        pre_start = pre_end = pre_date_range

    pre_meta_df = fetch_meta_insights(
        pre_start.strftime("%Y-%m-%d"),
        pre_end.strftime("%Y-%m-%d"),
        campaign_id=PRETEST_CAMPAIGN_ID,
    )
    pre_sheet_df = fetch_sheet_data()

    pre_date_col = detect_date_column(pre_sheet_df) if not pre_sheet_df.empty else None
    if pre_date_col and not pre_sheet_df.empty:
        pre_sheet_df[pre_date_col] = parse_tw_datetime(pre_sheet_df[pre_date_col])
        filtered_sheet = pre_sheet_df[
            (pre_sheet_df[pre_date_col] >= pd.Timestamp(pre_start)) &
            (pre_sheet_df[pre_date_col] <= pd.Timestamp(pre_end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
        ]
        pre_daily_leads = (
            filtered_sheet.groupby(filtered_sheet[pre_date_col].dt.normalize())
            .size().reset_index(name="leads")
        ).rename(columns={pre_date_col: "date"})[["date", "leads"]]
        pre_total_leads = int(filtered_sheet.shape[0])
    else:
        pre_total_leads = int(pre_sheet_df.shape[0]) if not pre_sheet_df.empty else 0
        pre_daily_leads = pd.DataFrame()

    pre_total_spend       = pre_meta_df["spend"].sum() if not pre_meta_df.empty else 0.0
    pre_total_clicks      = int(pre_meta_df["clicks"].sum()) if not pre_meta_df.empty else 0
    pre_total_impressions = int(pre_meta_df["impressions"].sum()) if not pre_meta_df.empty else 0
    pre_avg_cpc           = (pre_total_spend / pre_total_clicks) if pre_total_clicks > 0 else 0.0
    pre_cpl               = (pre_total_spend / pre_total_leads) if pre_total_leads > 0 else 0.0

    p1, p2, p3, p4, p5, p6 = st.columns(6)
    p1.metric("總花費",     fmt_currency(pre_total_spend))
    p2.metric("總點擊數",   fmt_number(pre_total_clicks))
    p3.metric("總曝光數",   fmt_number(pre_total_impressions))
    p4.metric("平均 CPC",   f"NT$ {pre_avg_cpc:.2f}")
    p5.metric("前測名單數", fmt_number(pre_total_leads))
    p6.metric("名單成本",   fmt_currency(pre_cpl))

    if not pre_meta_df.empty:
        pre_chart = pre_meta_df.copy()
        if not pre_daily_leads.empty:
            pre_chart = pre_chart.merge(pre_daily_leads, on="date", how="left")
            pre_chart["leads"] = pre_chart["leads"].fillna(0).astype(int)
            pre_chart["cum_spend"] = pre_chart["spend"].cumsum()
            pre_chart["cum_leads"] = pre_chart["leads"].cumsum()
            pre_chart["daily_cost_per_lead"] = pre_chart.apply(
                lambda r: r["cum_spend"] / r["cum_leads"] if r["cum_leads"] > 0 else None, axis=1
            )
        else:
            pre_chart["daily_cost_per_lead"] = None

        pre_fig = go.Figure()
        pre_fig.add_trace(go.Bar(
            x=pre_chart["date"], y=pre_chart["spend"],
            name="每日花費 (NT$)", marker_color="#4C9BE8", yaxis="y1",
            hovertemplate="%{x|%Y-%m-%d}<br>花費：NT$ %{y:,.0f}<extra></extra>",
        ))
        if pre_chart["daily_cost_per_lead"].notna().any():
            pre_fig.add_trace(go.Scatter(
                x=pre_chart["date"], y=pre_chart["daily_cost_per_lead"],
                name="累積名單成本 (NT$)", mode="lines+markers",
                line=dict(color="#FF6B6B", width=2), marker=dict(size=6), yaxis="y2",
                hovertemplate="%{x|%Y-%m-%d}<br>名單成本：NT$ %{y:,.0f}<extra></extra>",
            ))
        pre_fig.update_layout(
            xaxis=dict(title="日期", tickformat="%m/%d"),
            yaxis=dict(title="每日花費 (NT$)", showgrid=False),
            yaxis2=dict(title="累積名單成本 (NT$)", overlaying="y", side="right", showgrid=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            hovermode="x unified", height=380,
            margin=dict(l=0, r=0, t=10, b=0),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(pre_fig, use_container_width=True)

        # ── 前測期每日明細表 ────────────────────────────────────────────────
        st.markdown("**每日明細**")
        pre_table = pre_meta_df.copy()
        if not pre_daily_leads.empty:
            pre_table = pre_table.merge(pre_daily_leads, on="date", how="left")
            pre_table["leads"] = pre_table["leads"].fillna(0).astype(int)
            pre_table["cost_per_lead"] = pre_table.apply(
                lambda r: r["spend"] / r["leads"] if r["leads"] > 0 else None, axis=1
            )
        else:
            pre_table["leads"] = 0
            pre_table["cost_per_lead"] = None

        pre_table["date_str"] = pre_table["date"].dt.strftime("%Y-%m-%d")

        pre_totals = {
            "date_str": "合計",
            "spend": pre_total_spend,
            "clicks": pre_total_clicks,
            "impressions": pre_total_impressions,
            "cpc": pre_avg_cpc,
            "leads": pre_total_leads,
            "cost_per_lead": pre_cpl if pre_total_leads > 0 else None,
        }
        pre_display = pd.concat(
            [pre_table[["date_str", "spend", "clicks", "impressions", "cpc", "leads", "cost_per_lead"]],
             pd.DataFrame([pre_totals])],
            ignore_index=True,
        )

        def _fmt(v, fn):
            return fn(v) if isinstance(v, (int, float)) and pd.notna(v) else "-"

        pre_display["spend"]         = pre_display["spend"].apply(lambda v: _fmt(v, lambda x: f"NT$ {x:,.0f}"))
        pre_display["clicks"]        = pre_display["clicks"].apply(lambda v: _fmt(v, lambda x: f"{int(x):,}"))
        pre_display["impressions"]   = pre_display["impressions"].apply(lambda v: _fmt(v, lambda x: f"{int(x):,}"))
        pre_display["cpc"]           = pre_display["cpc"].apply(lambda v: _fmt(v, lambda x: f"NT$ {x:.2f}"))
        pre_display["leads"]         = pre_display["leads"].apply(lambda v: _fmt(v, lambda x: f"{int(x):,}"))
        pre_display["cost_per_lead"] = pre_display["cost_per_lead"].apply(lambda v: _fmt(v, lambda x: f"NT$ {x:,.0f}"))

        pre_display.columns = ["日期", "花費", "點擊", "曝光", "CPC", "名單數", "名單成本"]
        st.dataframe(pre_display, use_container_width=True, hide_index=True)
    else:
        st.info("前測期間內無廣告數據")
