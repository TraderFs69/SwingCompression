import pandas as pd
import requests
import os
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# =========================
# SECRETS
# =========================
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# =========================
# LOAD SP500
# =========================
def load_sp500():
    url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"

    try:
        df = pd.read_csv(url)
        print(f"Loaded {len(df)} tickers")
        return df

    except Exception as e:
        print("Erreur chargement S&P500:", e)
        return pd.DataFrame()

# =========================
# FETCH DATA
# =========================
def get_data(ticker, start, end, retries=2):

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/"
        f"{ticker}/range/1/day/{start}/{end}"
        f"?adjusted=true&sort=asc&limit=5000&apiKey={POLYGON_API_KEY}"
    )

    for attempt in range(retries):

        try:
            r = requests.get(url, timeout=10)

            if r.status_code != 200:
                return None

            data = r.json()

            if "results" not in data:
                return None

            df = pd.DataFrame(data["results"])

            if df.empty:
                return None

            df["Date"] = pd.to_datetime(df["t"], unit="ms")
            df.set_index("Date", inplace=True)

            df.rename(columns={
                "c": "close",
                "h": "high",
                "v": "volume"
            }, inplace=True)

            return df[["close", "high", "volume"]]

        except Exception as e:
            print(f"{ticker} error: {e}")
            time.sleep(1)

    return None

# =========================
# ANALYSE
# =========================
def analyze_stock(row, start, end):

    ticker = row["Symbol"]
    sector = row["GICS Sector"]

    df = get_data(ticker, start, end)

    if df is None or len(df) < 200:
        return None

    try:

        # EMA
        df["ema20"] = df["close"].ewm(span=20).mean()
        df["ema50"] = df["close"].ewm(span=50).mean()
        df["ema200"] = df["close"].ewm(span=200).mean()

        # Volume
        df["vol_avg"] = df["volume"].rolling(20).mean()

        # Highs
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
        # PRÉ-FILTRE
        # =========================
        if close < ema200:
            return None

        if volume < 300000:
            return None

        # =========================
        # CONDITIONS
        # =========================
        high_20_prev = df["high_20"].iloc[-2]
        high_5_prev = df["high"].rolling(5).max().iloc[-2]

        breakout = high >= high_20_prev * 0.99
        early = high >= high_5_prev * 0.99
        momentum = close > df["close"].iloc[-5]

        volume_ok = volume > vol_avg * 1.05
        not_extended = close / ema20 < 1.25

        # =========================
        # SCORE
        # =========================
        score = 0

        if close > ema50 > ema200:
            score += 25

        if breakout:
            score += 25

        if early:
            score += 15

        if momentum:
            score += 15

        if volume_ok:
            score += 10

        if not_extended:
            score += 10

        if score < 50:
            return None

        # =========================
        # RESULT
        # =========================
        return {
            "ticker": ticker,
            "sector": sector,
            "close": round(close, 2),
            "score": score,
            "type": (
                "BREAKOUT"
                if breakout else
                "EARLY"
                if early else
                "MOMENTUM"
            ),
            "volume_ratio": round(volume / vol_avg, 2)
        }

    except Exception as e:
        print(f"{ticker} analyse error: {e}")
        return None

# =========================
# MAIN
# =========================
def main():

    print("Starting TEA scanner...")

    # =========================
    # LOAD UNIVERSE
    # =========================
    universe = load_sp500()

    if universe.empty:
        print("Universe empty — STOP")
        return

    # =========================
    # DATES
    # =========================
    end = datetime.today().strftime("%Y-%m-%d")

    start = (
        datetime.today() - timedelta(days=300)
    ).strftime("%Y-%m-%d")

    results = []

    # =========================
    # MULTI THREAD SCAN
    # =========================
    with ThreadPoolExecutor(max_workers=10) as executor:

        futures = [
            executor.submit(analyze_stock, row, start, end)
            for _, row in universe.iterrows()
        ]

        for f in futures:

            try:
                res = f.result()

                if res:
                    results.append(res)

            except Exception as e:
                print("Future error:", e)

    print(f"Valid setups found: {len(results)}")

    # =========================
    # FALLBACK
    # =========================
    if not results:

        print("No setups — fallback momentum")

        fallback = []

        for _, row in universe.head(50).iterrows():

            df = get_data(row["Symbol"], start, end)

            if df is None or len(df) < 50:
                continue

            try:
                if df["close"].iloc[-1] > df["close"].iloc[-5]:
                    fallback.append(row["Symbol"])

            except:
                continue

        msg = "⚠️ Aucun breakout — marché faible\n\n"
        msg += "🔥 Momentum fallback:\n\n"
        msg += "\n".join(fallback[:10])

    else:

        # =========================
        # SORT
        # =========================
        results = sorted(
            results,
            key=lambda x: x["score"],
            reverse=True
        )[:15]

        # =========================
        # SECTORS
        # =========================
        sector_count = pd.Series(
            [r["sector"] for r in results]
        ).value_counts()

        # =========================
        # MESSAGE
        # =========================
        msg = "🟫 TEA SCANNER\n\n"

        msg += "🏭 Secteurs dominants:\n"

        for s, count in sector_count.head(3).items():
            msg += f"{s} ({count})\n"

        msg += "\n🎯 Top setups:\n\n"

        for r in results:

            msg += (
                f"{r['ticker']} | "
                f"{r['sector']} | "
                f"{r['type']} | "
                f"{r['close']}$ | "
                f"Score {r['score']} | "
                f"Vol x{r['volume_ratio']}\n"
            )

    # =========================
    # DISCORD
    # =========================
    if DISCORD_WEBHOOK_URL:

        try:
            requests.post(
                DISCORD_WEBHOOK_URL,
                json={"content": msg},
                timeout=10
            )

            print("Sent to Discord")

        except Exception as e:
            print("Discord failed:", e)

    else:
        print("No Discord webhook")

# =========================
# RUN
# =========================
if __name__ == "__main__":
    main()
