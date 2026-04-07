import pandas as pd
import numpy as np
import requests
import os
from datetime import datetime, timedelta

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# =========================
# LOAD UNIVERSE
# =========================
def load_universe():
    df = pd.read_csv("https://datahub.io/core/s-and-p-500-companies/r/constituents.csv")
    return df["Symbol"].str.replace(".", "-", regex=False).tolist()

# =========================
# FETCH DATA
# =========================
def get_data(ticker, start, end):
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}?adjusted=true&sort=asc&limit=5000&apiKey={POLYGON_API_KEY}"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        if "results" not in data:
            return None

        df = pd.DataFrame(data["results"])
        df["Date"] = pd.to_datetime(df["t"], unit="ms")
        df.set_index("Date", inplace=True)

        df.rename(columns={"c":"close","v":"volume"}, inplace=True)
        return df[["close","volume"]]
    except:
        return None

# =========================
# INDICATORS
# =========================
def add_indicators(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["ema200"] = df["close"].ewm(span=200).mean()
    df["vol_avg"] = df["volume"].rolling(20).mean()
    df["high_20"] = df["close"].rolling(20).max()
    df["low_20"] = df["close"].rolling(20).min()
    return df

# =========================
# BREAKOUT DETECTION
# =========================
def analyze(df):
    if len(df) < 220:
        return None

    df = add_indicators(df)
    last = df.iloc[-1]

    close = last["close"]
    ema20 = last["ema20"]
    ema50 = last["ema50"]
    ema200 = last["ema200"]
    volume = last["volume"]
    vol_avg = last["vol_avg"]

    high_20_prev = df["high_20"].iloc[-2]
    low_20_prev = df["low_20"].iloc[-2]

    # =========================
    # 1. TREND FILTER
    # =========================
    if not (close > ema50 > ema200):
        return None

    # =========================
    # 2. COMPRESSION (KEY)
    # =========================
    range_pct = (high_20_prev - low_20_prev) / low_20_prev
    compression = range_pct < 0.25  # range serré

    if not compression:
        return None

    # =========================
    # 3. BREAKOUT (REALISTIC)
    # =========================
    breakout = close >= high_20_prev * 0.98

    # =========================
    # 4. VOLUME CONFIRMATION
    # =========================
    volume_ok = volume > vol_avg * 1.2

    # =========================
    # 5. EXTENSION CONTROL
    # =========================
    not_extended = close / ema20 < 1.15

    # =========================
    # FINAL DECISION
    # =========================
    if breakout and volume_ok and not_extended:
        score = 0

        score += 30  # trend
        score += 25  # breakout
        score += 20  # volume
        score += 15  # compression
        score += 10  # rr

        return {
            "close": round(close,2),
            "score": score,
            "volume_ratio": round(volume / vol_avg,2),
            "range_pct": round(range_pct,2)
        }

    return None

# =========================
# DISCORD OUTPUT
# =========================
def send_discord(results):
    if not results:
        msg = "⚠️ Aucun breakout propre aujourd’hui"
    else:
        msg = "🟫 TEA BREAKOUT SCANNER\n\n"

        for r in results:
            msg += f"{r['ticker']} | {r['close']}$ | Score {r['score']} | Vol x{r['volume_ratio']}\n"

    requests.post(DISCORD_WEBHOOK_URL, json={"content": msg})

# =========================
# MAIN
# =========================
def main():
    tickers = load_universe()

    end = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=300)).strftime("%Y-%m-%d")

    results = []

    for ticker in tickers:
        df = get_data(ticker, start, end)
        if df is None:
            continue

        res = analyze(df)
        if res:
            res["ticker"] = ticker
            results.append(res)

    # TRI
    results = sorted(results, key=lambda x: x["score"], reverse=True)[:10]

    send_discord(results)

if __name__ == "__main__":
    main()
