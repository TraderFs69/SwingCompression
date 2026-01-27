import streamlit as st
import pandas as pd
import requests
from datetime import date, timedelta

# =====================================================
# CONFIG
# =====================================================
st.set_page_config(layout="wide")

POLYGON_KEY = st.secrets["POLYGON_API_KEY"]
DISCORD_WEBHOOK = st.secrets.get("DISCORD_WEBHOOK_URL")

LOOKBACK = 160
MIN_SCORE_STRICT = 65
MIN_SCORE_WATCH = 70
MIN_RR_STRICT = 1.5
MIN_RR_WATCH = 1.2

# =====================================================
# LOAD TICKERS — RUSSELL 3000
# =====================================================
@st.cache_data
def load_tickers():
    df = pd.read_excel("russell3000_constituents.xlsx")
    tickers = (
        df.iloc[:, 0]
        .dropna()
        .astype(str)
        .str.upper()
        .unique()
        .tolist()
    )
    return [t for t in tickers if t != "SYMBOL"]

TICKERS = load_tickers()

# =====================================================
# POLYGON — OHLC DAILY
# =====================================================
@st.cache_data(ttl=3600)
def get_ohlc(ticker):
    end = date.today()
    start = end - timedelta(days=LOOKBACK)

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start}/{end}?adjusted=true&sort=asc&limit=50000&apiKey={POLYGON_KEY}"
    )

    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        if "results" not in data:
            return None
        df = pd.DataFrame(data["results"])
        df["Close"] = df["c"]
        return df
    except Exception:
        return None

# =====================================================
# EARNINGS FLAG (Polygon)
# =====================================================
@st.cache_data(ttl=86400)
def has_earnings_near(ticker, window=1):
    today = date.today()
    start = today - timedelta(days=window)
    end = today + timedelta(days=window)

    url = (
        f"https://api.polygon.io/v3/reference/earnings?"
        f"ticker={ticker}&from={start}&to={end}&apiKey={POLYGON_KEY}"
    )

    try:
        r = requests.get(url, timeout=5)
        if r.status_code != 200:
            return False
        data = r.json()
        return bool(data.get("results"))
    except Exception:
        return False

# =====================================================
# INDICATORS
# =====================================================
def EMA(s, n):
    return s.ewm(span=n, adjust=False).mean()

def ATR(df, n):
    tr = pd.concat([
        df["h"] - df["l"],
        (df["h"] - df["Close"].shift()).abs(),
        (df["l"] - df["Close"].shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()

# =====================================================
# MODEL 3 — STRICT / WATCH
# =====================================================
def model3(df):
    if len(df) < 70:
        return None

    c = df["Close"]
    h, l, v = df["h"], df["l"], df["v"]

    atr14 = ATR(df, 14)
    atr40 = ATR(df, 40)
    ema20 = EMA(c, 20)
    ema50 = EMA(c, 50)

    bb_mid = c.rolling(20).mean()
    bb_std = c.rolling(20).std()
    bb_width = (bb_std * 4) / bb_mid

    range_high = h.rolling(10).max()
    range_low = l.rolling(10).min()
    median_range = (range_high - range_low).rolling(40).median()

    vol_mean = v.rolling(20).mean()

    i = -1
    score = 0
    score += atr14.iloc[i] < atr40.iloc[i]
    score += atr14.iloc[i] <= atr14.iloc[i-10] * 1.05
    score += (range_high.iloc[i-1] - range_low.iloc[i-1]) < median_range.iloc[i]
    score += bb_width.iloc[i] < bb_width.rolling(40).median().iloc[i]
    score += v.iloc[i] < vol_mean.iloc[i]
    score += c.iloc[i] > ema20.iloc[i]
    score += c.iloc[i] > ema50.iloc[i]

    score_norm = round(score / 7 * 100, 2)

    status = "🚀 TRIGGER" if c.iloc[i] > range_high.iloc[i-1] else "🟡 SETUP"

    return {
        "Status": status,
        "Score": score_norm,
        "ATR": atr14.iloc[i],
        "RangeLow": range_low.iloc[i],
        "Close": c.iloc[i]
    }

# =====================================================
# DISCORD
# =====================================================
def send_to_discord(title, rows):
    if not DISCORD_WEBHOOK or not rows:
        return

    msg = f"**{title}**\n\n"
    for r in rows[:20]:
        msg += (
            f"{r['Tag']} **{r['Ticker']}** @ ${r['Price']} | "
            f"Score `{r['Score']}` | R:R `{r['RR']}`\n"
        )

    requests.post(DISCORD_WEBHOOK, json={"content": msg})

# =====================================================
# UI
# =====================================================
st.title("🚨 Modèle 3 — STRICT & WATCHLIST (Earnings-Aware)")

limit = st.slider("Nombre de tickers", 50, len(TICKERS), 300)

if st.button("🚀 Scanner"):
    strict, watch = [], []

    with st.spinner("Scan en cours…"):
        for t in TICKERS[:limit]:
            df = get_ohlc(t)
            if df is None:
                continue

            m = model3(df)
            if not m:
                continue

            earnings = has_earnings_near(t)
            price = round(m["Close"], 2)
            atr = m["ATR"]
            sl = round(m["RangeLow"] - 0.2 * atr, 2)
            tp = round(price + 2 * atr, 2)
            rr = round((tp - price) / (price - sl), 2) if price > sl else 0

            # STRICT
            if (
                m["Status"] == "🚀 TRIGGER"
                and m["Score"] >= MIN_SCORE_STRICT
                and rr >= MIN_RR_STRICT
                and not earnings
            ):
                strict.append({
                    "Ticker": t,
                    "Price": price,
                    "Score": m["Score"],
                    "RR": rr,
                    "Tag": "🚨"
                })

            # WATCH
            elif (
                m["Status"] == "🟡 SETUP"
                and m["Score"] >= MIN_SCORE_WATCH
                and rr >= MIN_RR_WATCH
            ):
                watch.append({
                    "Ticker": t,
                    "Price": price,
                    "Score": m["Score"],
                    "RR": rr,
                    "Tag": "⚠️ Earnings" if earnings else "🟡"
                })

    if strict:
        st.subheader("🚨 STRICT TRIGGERS")
        st.dataframe(pd.DataFrame(strict))
        send_to_discord("🚨 STRICT TRIGGERS", strict)

    if watch:
        st.subheader("🟡 WATCHLIST")
        st.dataframe(pd.DataFrame(watch))
        send_to_discord("🟡 WATCHLIST", watch)

    if not strict and not watch:
        st.info("Aucun signal aujourd’hui.")
