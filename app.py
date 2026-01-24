import streamlit as st
import pandas as pd
import requests

# =====================================================
# CONFIG
# =====================================================
st.set_page_config(layout="wide")

POLYGON_KEY = st.secrets["POLYGON_API_KEY"]
DISCORD_WEBHOOK = st.secrets.get("DISCORD_WEBHOOK_URL")

LOOKBACK = 140
MIN_SCORE = 55
MIN_RR = 1.3
SETUP_DISTANCE = 0.98  # 2 % sous résistance

# =====================================================
# LOAD TICKERS — RUSSELL 3000 (COLONNE A = Symbol)
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
# POLYGON — OHLC DAILY (STRUCTURE)
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
        if r.status_code != 200 or not r.text or r.text[0] != "{":
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
# POLYGON — PRIX MARCHÉ CORRECT (SNAPSHOT day.c)
# =====================================================
@st.cache_data(ttl=60)
def get_market_price(ticker):
    url = (
        f"https://api.polygon.io/v2/snapshot/locale/us/"
        f"markets/stocks/tickers/{ticker}?apiKey={POLYGON_KEY}"
    )
    try:
        r = requests.get(url, timeout=5)
        if r.status_code != 200:
            return None
        data = r.json()
        t = data.get("ticker", {})
        if "day" in t and "c" in t["day"]:
            return round(t["day"]["c"], 2)
        return None
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
# MODÈLE 3 — SETUP (DISTANCES, PAS DE PRIX ABSOLUS)
# =====================================================
def model3_setup(df):
    if len(df) < 60:
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

    range_high = h.rolling(10).max().iloc[-2]
    range_low = l.rolling(10).min().iloc[-2]
    median_range = (h.rolling(10).max() - l.rolling(10).min()).rolling(40).median()

    vol_mean = v.rolling(20).mean()

    i = -1
    score = 0

    # Compression
    score += atr14.iloc[i] < atr40.iloc[i]
    score += atr14.iloc[i] <= atr14.iloc[i-10] * 1.05
    score += (range_high - range_low) < median_range.iloc[i]
    score += bb_width.iloc[i] < bb_width.rolling(40).median().iloc[i]
    score += v.iloc[i] < vol_mean.iloc[i]

    # Proximité breakout
    if c.iloc[i] >= range_high * SETUP_DISTANCE:
        score += 2

    # Structure
    score += c.iloc[i] > ema20.iloc[i]
    score += c.iloc[i] > ema50.iloc[i]

    score_norm = round(score / 11 * 100, 2)

    status = "🚀 TRIGGER" if c.iloc[i] > range_high else "🟡 SETUP"

    return {
        "Status": status,
        "Score": score_norm,
        "ATR": atr14.iloc[i],
        "RangeLow": range_low,
        "DailyClose": c.iloc[i]
    }

# =====================================================
# DISCORD
# =====================================================
def send_to_discord(df):
    if not DISCORD_WEBHOOK or df.empty:
        return

    lines = []
    for _, r in df.iterrows():
        lines.append(
            f"{r['Status']} **{r['Ticker']}** @ ${r['Price']} | "
            f"Score `{r['Score']}` | "
            f"R:R `{r['R:R']}` | "
            f"SL `{r['SL']}` → TP `{r['TP1']}`"
        )

    message = (
        "📊 **Modèle 3 — Compression → Expansion (TP/SL alignés prix marché)**\n\n"
        + "\n".join(lines[:20])
    )

    payload = {"content": message[:1900]}
    try:
        requests.post(DISCORD_WEBHOOK, json=payload, timeout=5)
    except Exception:
        pass

# =====================================================
# UI
# =====================================================
st.title("📦 Modèle 3 — TP / SL alignés sur le prix marché")

limit = st.slider("Nombre de tickers à analyser", 50, len(TICKERS), 300)

if st.button("🚀 Scanner et envoyer sur Discord"):
    rows = []

    with st.spinner("Scan en cours…"):
        for t in TICKERS[:limit]:
            df = get_ohlc(t)
            if df is None:
                continue

            m3 = model3_setup(df)
            if not m3 or m3["Score"] < MIN_SCORE:
                continue

            price = get_market_price(t)
            if price is None:
                price = round(m3["DailyClose"], 2)

            atr = m3["ATR"]

            # SL recalé sous la base + ajustement prix
            sl = round(m3["RangeLow"] - 0.2 * atr + (price - m3["DailyClose"]), 2)

            # TP dynamiques depuis le prix marché
            tp1 = round(price + 2 * atr, 2)
            tp2 = round(price + 3 * atr, 2)

            risk = price - sl
            rr = round((tp1 - price) / risk, 2) if risk > 0 else None

            if rr is None or rr < MIN_RR:
                continue

            rows.append([
                t,
                price,
                m3["Status"],
                m3["Score"],
                price,
                sl,
                tp1,
                tp2,
                rr
            ])

    if rows:
        result = pd.DataFrame(rows, columns=[
            "Ticker",
            "Price",
            "Status",
            "Score",
            "Entry",
            "SL",
            "TP1",
            "TP2",
            "R:R"
        ]).sort_values(["Status", "Score"], ascending=[True, False])

        st.dataframe(result, width="stretch")
        send_to_discord(result)
        st.success("Scan terminé et envoyé sur Discord ✅")
    else:
        st.info("Aucun setup valide avec les critères actuels.")
