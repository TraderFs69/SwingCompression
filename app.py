import pandas as pd
import requests
import os
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# =========================
# LOAD UNIVERSE (ANTI BUG)
# =========================
def load_universe():
    try:
        url = "https://datahub.io/core/s-and-p-500-companies/r/constituents.csv"
        df = pd.read_csv(url)
        df["Sector"] = "Unknown"
        print(f"Universe loaded: {len(df)} stocks")
        return df[["Symbol", "Sector"]]
    except:
        print("ERROR loading universe")
        return pd.DataFrame(columns=["Symbol", "Sector"])

# =========================
# FETCH DATA (RETRY)
# =========================
def get_data(ticker, start, end, retries=2):
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}?adjusted=true&sort=asc&limit=5000&apiKey={POLYGON_API_KEY}"

    for attempt in range(retries):
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
            time.sleep(1)

    return None

# =========================
# ANALYSE
# =========================
def analyze_stock(row, start, end):
    ticker = row["Symbol"]
    sector = row["Sector"]

    df = get_data(ticker, start, end)
    if df is None or len(df) < 200:
        return None

    try:
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

        # PRÉ-FILTRE
        if close < ema200 or volume < 300000:
            return None

        high_20_prev = df["high_20"].iloc[-2]
        high_5_prev = df["high"].rolling(5).max().iloc[-2]

        breakout = high >= high_20_prev * 0.99
        early = high >= high_5_prev * 0.99
        momentum = close > df["close"].iloc[-5]

        volume_ok = volume > vol_avg * 1.05
        not_extended = close / ema20 < 1.25

        score = 0
        if close > ema50 > ema200: score += 25
        if breakout: score += 25
        if early: score += 15
        if momentum: score += 15
        if volume_ok: score += 10
        if not_extended: score += 10

        if score < 50:
            return None

        return {
            "ticker": ticker,
            "sector": sector,
            "close": round(close, 2),
            "score": score,
            "type": (
                "BREAKOUT" if breakout else
                "EARLY" if early else
                "MOMENTUM"
            ),
            "volume_ratio": round(volume / vol_avg, 2)
        }

    except:
        return None

# =========================
# MAIN
# =========================
def main():
    print("Starting TEA scanner...")

    universe = load_universe()

    if universe.empty:
        print("Universe empty — STOP")
        return

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

    print(f"Valid setups found: {len(results)}")

    # =========================
    # FALLBACK SI 0
    # =========================
    if not results:
        print("No setups — fallback momentum")

        fallback = []
        for _, row in universe.head(50).iterrows():
            df = get_data(row["Symbol"], start, end)
            if df is None or len(df) < 50:
                continue

            if df["close"].iloc[-1] > df["close"].iloc[-5]:
                fallback.append(row["Symbol"])

        msg = "⚠️ Aucun breakout — marché faible\n\n🔥 Momentum fallback:\n"
        msg += "\n".join(fallback[:10])

    else:
        results = sorted(results, key=lambda x: x["score"], reverse=True)[:15]

        sector_count = pd.Series([r["sector"] for r in results]).value_counts()

        msg = "🟫 TEA SCANNER\n\n"

        msg += "🏭 Secteurs dominants:\n"
        for s, count in sector_count.head(3).items():
            msg += f"{s} ({count})\n"

        msg += "\n🎯 Top setups:\n\n"

        for r in results:
            msg += f"{r['ticker']} | {r['sector']} | {r['type']} | {r['close']}$ | Score {r['score']} | Vol x{r['volume_ratio']}\n"

    # =========================
    # DISCORD SAFE
    # =========================
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": msg})
        print("Sent to Discord")
    except:
        print("Discord failed")

# =========================
# RUN
# =========================
if __name__ == "__main__":
    main()
