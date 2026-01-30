# =====================================================
# MODELE 3 — STRICT / WATCHLIST + DISCORD (ROBUSTE)
# =====================================================
import streamlit as st
import pandas as pd
import requests
import time
from datetime import date, timedelta

# ================= CONFIG =================
st.set_page_config(layout="wide")
st.title("🚨 Modèle 3 — STRICT / WATCHLIST")

POLYGON_KEY = st.secrets["POLYGON_API_KEY"]
DISCORD_WEBHOOK = st.secrets["DISCORD_WEBHOOK_URL"]

LOOKBACK = 180
MIN_SCORE = 65
MIN_RR = 1.3
MAX_DISCORD_LEN = 1900

# ================= SESSION =================
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "TradingEnAction-Modele3/1.1"})

# ================= LOAD TICKERS =================
@st.cache_data
def load_tickers():
    df = pd.read_excel("russell3000_constituents.xlsx")
    return (
        df.iloc[:, 0]
        .dropna()
        .astype(str)
        .str.upper()
        .unique()
        .tolist()
    )

TICKERS = load_tickers()

# ================= POLYGON =================
def get_ohlc(ticker):
    end = date.today()
    start = end - timedelta(days=LOOKBACK)

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start}/{end}?adjusted=true&sort=asc&limit=50000&apiKey={POLYGON_KEY}"
    )

    try:
        r = SESSION.get(url, timeout=15)
        if r.status_code != 200:
            return None

        data = r.json()
        if not data.get("results"):
            return None

        df = pd.DataFrame(data["results"])
        df["Open"] = df["o"]
        df["High"] = df["h"]
        df["Low"] = df["l"]
        df["Close"] = df["c"]
        return df

    except Exception:
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

# ================= MODELE 3 =================
def modele3(df):
    if len(df) < 80:
        return None

    # on ignore la bougie du jour
    df = df.iloc[:-1]

    c, h, l = df["Close"], df["High"], df["Low"]

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

    score_pct = round(score / 4 * 100, 2)
    if score_pct < MIN_SCORE:
        return None

    price = round(c.iloc[i], 2)
    atr = atr14.iloc[i]
    sl = round(range_low.iloc[i] - 0.2 * atr, 2)
    tp = round(price + 2 * atr, 2)

    if price <= sl:
        return None

    rr = round((tp - price) / (price - sl), 2)
    if rr < MIN_RR:
        return None

    return {"Price": price, "Score": score_pct, "RR": rr}

# ================= DISCORD =================
def send_discord_modele3(df):
    if df.empty:
        r = requests.post(
            DISCORD_WEBHOOK,
            json={"content": "ℹ️ **Modèle 3 — Aucun setup valide aujourd’hui**"},
            timeout=10
        )
        return r.status_code == 204

    lines = ["🚨 **MODÈLE 3 — SETUPS VALIDES**\n"]

    for i, row in enumerate(df.itertuples(index=False), 1):
        lines.append(
            f"{i}️⃣ **{row.Ticker}** | "
            f"${row.Price} | "
            f"Score {row.Score} | "
            f"R:R {row.RR}"
        )

    lines.append("\n⏱ Scan Modèle 3 — Trading en Action")

    # split message si trop long
    chunks = []
    current = ""

    for line in lines:
        if len(current) + len(line) > MAX_DISCORD_LEN:
            chunks.append(current)
            current = ""
        current += line + "\n"

    chunks.append(current)

    for c in chunks:
        r = requests.post(DISCORD_WEBHOOK, json={"content": c}, timeout=10)
        if r.status_code != 204:
            st.error(f"❌ Discord ERROR {r.status_code}: {r.text}")
            return False
        time.sleep(0.3)

    return True

# ================= UI =================
limit = st.slider("Nombre de tickers analysés", 50, len(TICKERS), 200)

if st.button("🚀 Scanner Modèle 3"):
    rows = []
    progress = st.progress(0)

    for i, t in enumerate(TICKERS[:limit]):
        df = get_ohlc(t)
        if df is None:
            continue

        m = modele3(df)
        if not m:
            continue

        rows.append([t, m["Price"], m["Score"], m["RR"]])
        progress.progress((i + 1) / limit)

    df_out = pd.DataFrame(rows, columns=["Ticker", "Price", "Score", "RR"])

    st.write("📊 Setups trouvés :", len(df_out))

    if df_out.empty:
        st.warning("Aucun setup Modèle 3 aujourd’hui.")
    else:
        st.dataframe(df_out, use_container_width=True)

    if send_discord_modele3(df_out):
        st.success("✅ Résultats envoyés sur Discord")

