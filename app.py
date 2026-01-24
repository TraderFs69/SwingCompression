import streamlit as st
import pandas as pd
import requests

# =====================================================
# CONFIG
# =====================================================
st.set_page_config(layout="wide")

POLYGON_KEY = st.secrets["POLYGON_API_KEY"]
LOOKBACK = 140

MIN_SCORE = 60
MIN_RR = 1.5

# =====================================================
# LOAD TICKERS — RUSSELL 3000 (COLONNE A)
# =====================================================
@st.cache_data
def load_tickers():
    df = pd.read_excel("russell3000_constituents.xlsx", header=0)

    tickers = (
        df.iloc[:, 0]
        .dropna()
        .astype(str)
        .str.strip()
        .str.upper()
        .unique()
        .tolist()
    )

    return [t for t in tickers if t != "SYMBOL"]

TICKERS = load_tickers()

# =====================================================
# POLYGON OHLC — ROBUSTE
# =====================================================
@st.cache_data(ttl=3600)
def get_ohlc(ticker):
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{LOOKBACK}/2025-01-01"
        f"?adjusted=true&sort=asc&apiKey={POLYGON_KEY}"
    )

    try:
        r = requests.get(url, timeout=10)

        if r.status_code != 200:
            return None
        if not r.text or r.text[0] != "{":
            return None

        data = r.json()
        if "results" not in data or not data["results"]:
            return None

        df = pd.DataFrame(data["results"])
        df["Close"] = df["c"]
        return df

    except Exception:
        return None

# =====================================================
# INDICATEURS
# =====================================================
def EMA(s, n):
    return s.ewm(span=n, adjust=False).mean()

def ATR(df, n=14):
    tr = pd.concat([
        df["h"] - df["l"],
        (df["h"] - df["Close"].shift()).abs(),
        (df["l"] - df["Close"].shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()

# =====================================================
# MODÈLE 3 — COMPRESSION → EXPANSION (ASSOUPLI)
# =====================================================
def model3_volatility_expansion(df):
    if len(df) < 60:
        return None

    c = df["Close"]
    h, l, v = df["h"], df["l"], df["v"]

    atr14 = ATR(df, 14)
    atr40 = ATR(df, 40)

    ema20 = EMA(c, 20)
    ema50 = EMA(c, 50)

    # Bollinger width
    bb_mid = c.rolling(20).mean()
    bb_std = c.rolling(20).std()
    bb_width = (bb_std * 4) / bb_mid

    range_10 = h.rolling(10).max() - l.rolling(10).min()
    median_range = range_10.rolling(40).median()

    vol_mean = v.rolling(20).mean()

    i = -1
    score = 0

    # ---- Compression (plus tolérante) ----
    score += atr14.iloc[i] < atr40.iloc[i]
    score += atr14.iloc[i] <= atr14.iloc[i-10] * 1.05
    score += range_10.iloc[i] < median_range.iloc[i]
    score += bb_width.iloc[i] < bb_width.rolling(40).median().iloc[i]
    score += v.iloc[i] < vol_mean.iloc[i]

    # ---- Expansion ----
    breakout = c.iloc[i] > h.rolling(10).max().iloc[i-1]
    score += breakout * 2
    score += ((h.iloc[i] - l.iloc[i]) > 1.5 * atr14.iloc[i]) * 2
    score += (v.iloc[i] > 1.3 * vol_mean.iloc[i]) * 2

    # ---- Trend filter ----
    score += c.iloc[i] > ema20.iloc[i]
    score += c.iloc[i] > ema50.iloc[i]

    score_norm = round(score / 13 * 100, 2)

    if not breakout:
        return {
            "Score": score_norm,
            "Entry": None,
            "SL": None,
            "TP1": None,
            "TP2": None,
            "RR": None
        }

    entry = round(c.iloc[i], 2)
    atr = atr14.iloc[i]

    sl = round(min(
        entry - 1.5 * atr,
        l.rolling(10).min().iloc[i]
    ), 2)

    tp1 = round(entry + 2 * atr, 2)
    tp2 = round(entry + 3 * atr, 2)

    risk = entry - sl
    rr = round((tp1 - entry) / risk, 2) if risk > 0 else None

    return {
        "Score": score_norm,
        "Entry": entry,
        "SL": sl,
        "TP1": tp1,
        "TP2": tp2,
        "RR": rr
    }

# =====================================================
# UI
# =====================================================
st.title("📦 Modèle 3 — Compression → Expansion (Swing Trading)")

limit = st.slider(
    "Nombre de tickers à analyser",
    min_value=50,
    max_value=len(TICKERS),
    value=300
)

if st.button("🔍 Scanner le Modèle 3"):
    rows = []

    with st.spinner("Scan en cours…"):
        for t in TICKERS[:limit]:
            df = get_ohlc(t)
            if df is None:
                continue

            m3 = model3_volatility_expansion(df)

            if (
                m3
                and m3["Score"] >= MIN_SCORE
                and m3["RR"] is not None
                and m3["RR"] >= MIN_RR
            ):
                rows.append([
                    t,
                    round(df["Close"].iloc[-1], 2),
                    m3["Score"],
                    m3["Entry"],
                    m3["SL"],
                    m3["TP1"],
                    m3["TP2"],
                    m3["RR"]
                ])

    if rows:
        result = pd.DataFrame(rows, columns=[
            "Ticker",
            "Price",
            "Score",
            "Entry",
            "SL",
            "TP1",
            "TP2",
            "R:R"
        ]).sort_values("Score", ascending=False)

        st.dataframe(result, width="stretch")
    else:
        st.info("Aucun setup valide avec les critères actuels.")
