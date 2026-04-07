import pandas as pd
import requests
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# =========================
# LOAD RUSSELL (LOCAL FILE)
# =========================
def load_universe():
    df = pd.read_excel("russell3000_constituents.xlsx")
    return df[["Symbol", "Sector"]].dropna()

# =========================
# FETCH DATA
# =========================
def get_data(ticker, start, end):
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}?adjusted=true&sort=asc&limit=5000&apiKey={POLYGON_API_KEY}"
    try:
        r = requests.get(url, timeout=5)
        data = r.json()
        if "results" not in data:
            return None

        df = pd.DataFrame(data["results"])
        df["Date"] = pd.to_datetime(df["t"], unit="ms")
        df.set_index("Date", inplace=True)

        df.rename(columns={
            "c": "close",
            "h": "high",
            "v": "volume"
        }, inplace=True)

        return df[["close", "high", "volume"]]

    except:
        return None

# =========================
# ANALYSE RAPIDE
# =========================
def analyze_stock(row, start, end):
    ticker = row["Symbol"]
    sector = row["Sector"]

    df = get_data(ticker, start, end)
    if df is None or len(df) < 220:
        return None

    # INDICATEURS
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["ema200"] = df["close"].ewm(span=200).mean()
    df["vol_avg"] = df["volume"].rolling(20).mean()
    df["high_20"] = df["high"].rolling(20).max()

    last = df.iloc[-1]

    close = last["close"]
    high = last["high"]
    ema20 = last["ema20"]
    ema50 = last["ema50"]
    ema200 = last["ema200"]
    volume = last["volume"]
    vol_avg = last["vol_avg"]

    # =========================
    # PRÉ-FILTRE (ULTRA IMPORTANT)
    # =========================
    if close < ema200:
        return None

    if volume < 500000:
        return None

    # =========================
    # BREAKOUT LOGIC
    # =========================
    high_20_prev = df["high_20"].iloc[-2]
    high_5_prev = df["high"].rolling(5).max().iloc[-2]

    breakout = high >= high_20_prev * 0.99
    early = high >= high_5_prev * 0.99
    momentum = close > df["close"].iloc[-5]

    volume_ok = volume > vol_avg * 1.1
    not_extended = close / ema20 < 1.20

    # =========================
    # SCORE
    # =========================
    score = 0

    if close > ema50 > ema200: score += 25
    if breakout: score += 25
    if early: score += 15
    if momentum: score += 15
    if volume_ok: score += 10
    if not_extended: score += 10

    if score < 60:
        return None

    setup_type = (
        "BREAKOUT" if breakout else
        "EARLY" if early else
        "MOMENTUM"
    )

    return {
        "ticker": ticker,
        "sector": sector,
        "close": round(close, 2),
        "score": score,
        "type": setup_type,
        "volume_ratio": round(volume / vol_avg, 2)
    }

# =========================
# MAIN MULTI-THREAD
# =========================
def main():
    universe = load_universe()

    end = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=300)).strftime("%Y-%m-%d")

    results = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(analyze_stock, row, start, end)
            for _, row in universe.iterrows()
        ]

        for f in futures:
            res = f.result()
            if res:
                results.append(res)

    # =========================
    # TRI
    # =========================
    results = sorted(results, key=lambda x: x["score"], reverse=True)[:20]

    # =========================
    # SECTEURS DOMINANTS
    # =========================
    sector_count = pd.Series([r["sector"] for r in results]).value_counts()

    # =========================
    # DISCORD MESSAGE
    # =========================
    if not results:
        msg = "⚠️ Aucun setup valide aujourd’hui"
    else:
        msg = "🟫 TEA SCANNER — RUSSELL\n\n"

        msg += "🏭 Secteurs dominants:\n"
        for s, count in sector_count.head(3).items():
            msg += f"{s} ({count})\n"

        msg += "\n🎯 Top setups:\n\n"

        for r in results:
            msg += f"{r['ticker']} | {r['sector']} | {r['type']} | {r['close']}$ | Score {r['score']} | Vol x{r['volume_ratio']}\n"

    requests.post(DISCORD_WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    main()
