# =====================================================
# MODELE 3 ELITE — TEA SCANNER (ULTRA RAPIDE)
# =====================================================
import streamlit as st
import pandas as pd
import requests
import time
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= CONFIG =================
st.set_page_config(layout="wide")
st.title("🚨 TEA — MODELE 3 ELITE")

POLYGON_KEY = st.secrets["POLYGON_API_KEY"]
DISCORD_WEBHOOK = st.secrets["DISCORD_WEBHOOK_URL"]

LOOKBACK = 160
MIN_SCORE = 65
TOP_N = 15
MAX_WORKERS = 12

SESSION = requests.Session()

# ================= LOAD TICKERS =================
@st.cache_data
def load_universe():
    df = pd.read_csv("https://datahub.io/core/s-and-p-500-companies/r/constituents.csv")
    return df[["Symbol", "Security", "GICS Sector"]]

UNIVERSE = load_universe()

# ================= DATA =================
def get_data(ticker):
    try:
        end = date.today()
        start = end - timedelta(days=LOOKBACK)

        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}?adjusted=true&sort=asc&limit=5000&apiKey={POLYGON_KEY}"

        r = SESSION.get(url, timeout=10)
        if r.status_code != 200:
            return None

        data = r.json()
        if "results" not in data:
            return None

        df = pd.DataFrame(data["results"])
        df["Close"] = df["c"]
        df["High"] = df["h"]
        df["Low"] = df["l"]
        df["Volume"] = df["v"]

        return df

    except:
        return None

# ================= INDICATEURS =================
def EMA(s, n):
    return s.ewm(span=n, adjust=False).mean()

def ATR(df, n):
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"] - df["Close"].shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()

# ================= SCORE ELITE =================
def compute_score(df):
    if len(df) < 80:
        return None

    df = df.iloc[:-1]

    c = df["Close"]

    ema20 = EMA(c, 20)
    ema50 = EMA(c, 50)
    ema200 = EMA(c, 200)

    atr14 = ATR(df, 14)
    atr40 = ATR(df, 40)

    vol = df["Volume"]
    rvol = vol.iloc[-1] / vol.rolling(20).mean().iloc[-1]

    range_high = df["High"].rolling(10).max()

    i = -1

    score = 0

    # 🔥 TREND
    if c.iloc[i] > ema200.iloc[i]:
        score += 25

    if c.iloc[i] > ema50.iloc[i]:
        score += 15

    if c.iloc[i] > ema20.iloc[i]:
        score += 10

    # 🔥 MOMENTUM
    if c.iloc[i] > range_high.iloc[i-1]:
        score += 20

    # 🔥 VOLATILITY COMPRESSION
    if atr14.iloc[i] < atr40.iloc[i]:
        score += 10

    # 🔥 VOLUME
    if rvol > 1.2:
        score += 10

    # 🔥 TREND SLOPE
    if ema20.iloc[i] > ema20.iloc[i-5]:
        score += 10

    return score

# ================= TRADE =================
def build_trade(df):
    c = df["Close"]
    atr = ATR(df, 14)

    price = c.iloc[-2]
    sl = price - atr.iloc[-2]
    tp = price + 2 * atr.iloc[-2]

    rr = (tp - price) / (price - sl)

    return round(price,2), round(sl,2), round(tp,2), round(rr,2)

# ================= WORKER =================
def process_row(row):
    ticker = row["Symbol"]

    df = get_data(ticker)
    if df is None:
        return None

    score = compute_score(df)
    if score is None or score < MIN_SCORE:
        return None

    price, sl, tp, rr = build_trade(df)

    if rr < 1.3:
        return None

    return {
        "Ticker": ticker,
        "Price": price,
        "Score": score,
        "RR": rr,
        "Sector": row["GICS Sector"],
        "Name": row["Security"]
    }

# ================= SCAN =================
if st.button("🚀 Lancer Scan ELITE"):

    results = []
    progress = st.progress(0)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(process_row, row) for _, row in UNIVERSE.iterrows()]

        for i, future in enumerate(as_completed(futures)):
            res = future.result()
            if res:
                results.append(res)

            progress.progress((i + 1) / len(UNIVERSE))

    df = pd.DataFrame(results)

    if df.empty:
        st.warning("Aucun setup trouvé.")
    else:
        df = df.sort_values("Score", ascending=False).head(TOP_N)
        st.dataframe(df, use_container_width=True)

# ================= DISCORD =================
        msg = "🚨 **TEA ELITE — TOP SETUPS**\n\n"

        for i, row in df.iterrows():
            msg += (
                f"{row['Ticker']} | ${row['Price']} | "
                f"Score {row['Score']} | R:R {row['RR']}\n"
                f"{row['Sector']} — {row['Name']}\n\n"
            )

        requests.post(DISCORD_WEBHOOK, json={"content": msg})

