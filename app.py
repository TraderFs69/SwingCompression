# =====================================================
# MODÈLE 3 — STRICT / WATCHLIST (STABLE)
# =====================================================
import streamlit as st
import pandas as pd
import requests
import time
from datetime import date, timedelta

# ---------------- CONFIG ----------------
st.set_page_config(layout="wide")
st.title("🚨 Modèle 3 — STRICT / WATCHLIST")

POLYGON_KEY = st.secrets["POLYGON_API_KEY"]

LOOKBACK = 160
MIN_SCORE = 65
MIN_RR = 1.3

# ---------------- SESSION ----------------
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "TradingEnAction-Scanner/1.0"})

# ---------------- LOAD TICKERS ----------------
@st.cache_data
def load_tickers():
    df = pd.read_excel("russell3000_constituents.xlsx")
    t = df.iloc[:, 0].dropna().astype(str).str.upper()
    return [x for x in t.unique() if x != "SYMBOL"]

TICKERS = load_tickers()

# ---------------- POLYGON ----------------
def get_ohlc(ticker, retries=2):
    end = date.today()
    start = end - timedelta(days=LOOKBACK)

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start}/{end}?adjusted=true&sort=asc&limit=50000&apiKey={POLYGON_KEY}"
    )

    for _ in range(retries):
        try:
            r = SESSION.get(url, timeout=20)
            if r.status_code != 200:
                return None

            data = r.json()
            if not data.get("results"):
                return None

            df = pd.DataFrame(data["results"])
            df["Close"] = df["c"]
            return df

        except requests.exceptions.Timeout:
            time.sleep(0.5)

        except Exception:
            return None

    return None

# ---------------- INDICATEURS ----------------
def EMA(s, n): return s.ewm(span=n, adjust=False).mean()

def ATR(df, n):
    tr = pd.concat([
        df["h"] - df["l"],
        (df["h"] - df["Close"].shift()).abs(),
        (df["l"] - df["Close"].shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()

# ---------------- LOGIQUE MODÈLE 3 ----------------
def model3(df):
    if len(df) < 70:
        return None

    c, h, l = df["Close"], df["h"], df["l"]

    atr14 = ATR(df, 14)
    atr40 = ATR(df, 40)
    ema20 = EMA(c, 20)
    ema50 = EMA(c, 50)

    range_high = h.rolling(10).max()
    range_low = l.rolling(10).min()

    i = -1
    score = sum([
        atr14.iloc[i] < atr40.iloc[i],
        c.iloc[i] > ema20.iloc[i],
        c.iloc[i] > ema50.iloc[i],
        c.iloc[i] > range_high.iloc[i - 1]
    ])

    return {
        "Score": round(score / 4 * 100, 2),
        "ATR": atr14.iloc[i],
        "RangeLow": range_low.iloc[i],
        "Close": c.iloc[i]
    }

# ---------------- SCAN ----------------
def scan_model3(tickers):
    rows = []
    progress = st.progress(0)

    for i, t in enumerate(tickers):
        df = get_ohlc(t)
        if df is None or len(df) < 100:
            continue

        m = model3(df)
        if not m or m["Score"] < MIN_SCORE:
            continue

        price = round(m["Close"], 2)
        sl = round(m["RangeLow"] - 0.2 * m["ATR"], 2)
        tp = round(price + 2 * m["ATR"], 2)
        rr = round((tp - price) / (price - sl), 2) if price > sl else 0

        if rr < MIN_RR:
            continue

        rows.append([t, price, m["Score"], rr])
        progress.progress((i + 1) / len(tickers))

    return pd.DataFrame(rows, columns=["Ticker", "Price", "Score", "R:R"])

# ---------------- UI ----------------
limit = st.slider("Nombre de tickers", 50, len(TICKERS), 200)

if st.button("🚀 Scanner Modèle 3"):
    df = scan_model3(TICKERS[:limit])
    if df.empty:
        st.info("Aucun signal aujourd’hui.")
    else:
        st.dataframe(df, use_container_width=True)
