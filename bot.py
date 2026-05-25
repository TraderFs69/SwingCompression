import pandas as pd
import requests
import os
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# ==========================================
# LOAD RUSSELL 3000
# ==========================================
def load_universe():

    try:
        df = pd.read_excel("russell3000_constituents.xlsx")

        # IMPORTANT
        # adapte selon le vrai nom de colonne
        if "Symbol" not in df.columns:
            possible = [c for c in df.columns if "sym" in c.lower()]
            if possible:
                df.rename(columns={possible[0]: "Symbol"}, inplace=True)

        if "Sector" not in df.columns:
            df["Sector"] = "Unknown"

        # nettoyage ticker
        df["Symbol"] = (
            df["Symbol"]
            .astype(str)
            .str.strip()
            .str.upper()
            .str.replace(".", "-", regex=False)
        )

        df = df.dropna(subset=["Symbol"])

        print(f"Universe loaded: {len(df)} stocks")

        return df[["Symbol", "Sector"]]

    except Exception as e:
        print("ERROR loading Russell 3000:", e)
        return pd.DataFrame(columns=["Symbol", "Sector"])


# ==========================================
# FETCH DATA
# ==========================================
def get_data(ticker, start, end, retries=3):

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}"
        f"/range/1/day/{start}/{end}"
        f"?adjusted=true&sort=asc&limit=5000"
        f"&apiKey={POLYGON_API_KEY}"
    )

    for attempt in range(retries):

        try:
            r = requests.get(url, timeout=15)

            if r.status_code != 200:
                return None

            data = r.json()

            if "results" not in data:
                return None

            if len(data["results"]) < 50:
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

        except Exception:
            time.sleep(1)

    return None


# ==========================================
# ANALYZE STOCK
# ==========================================
def analyze_stock(row, start, end):

    ticker = row["Symbol"]
    sector = row["Sector"]

    df = get_data(ticker, start, end)

    if df is None:
        return None

    if len(df) < 200:
        return None

    try:

        # ======================
        # INDICATORS
        # ======================

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

        # ======================
        # FILTERS
        # ======================

        if pd.isna(ema200):
            return None

        if close < ema200:
            return None

        if volume < 300000:
            return None

        if pd.isna(vol_avg):
            return None

        # ======================
        # BREAKOUT LOGIC
        # ======================

        high_20_prev = df["high_20"].iloc[-2]
        high_5_prev = df["high"].rolling(5).max().iloc[-2]

        breakout = high >= high_20_prev * 0.99
        early = high >= high_5_prev * 0.99
        momentum = close > df["close"].iloc[-5]

        volume_ok = volume > vol_avg * 1.05
        not_extended = close / ema20 < 1.25

        # ======================
        # SCORE
        # ======================

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

        setup_type = (
            "BREAKOUT"
            if breakout else
            "EARLY"
            if early else
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

    except Exception:
        return None


# ==========================================
# MAIN
# ==========================================
def main():

    print("Starting TEA scanner...")

    universe = load_universe()

    if universe.empty:
        print("Universe empty")
        return

    end = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=400)).strftime("%Y-%m-%d")

    results = []

    # ======================================
    # THREADING
    # ======================================

    with ThreadPoolExecutor(max_workers=5) as executor:

        futures = [
            executor.submit(analyze_stock, row, start, end)
            for _, row in universe.iterrows()
        ]

        completed = 0

        for future in as_completed(futures):

            completed += 1

            if completed % 100 == 0:
                print(f"Processed {completed} stocks")

            try:
                res = future.result()

                if res:
                    results.append(res)

            except Exception:
                pass

    print(f"Valid setups found: {len(results)}")

    # ======================================
    # NO RESULTS
    # ======================================

    if not results:

        msg = "⚠️ Aucun setup trouvé aujourd'hui."

    else:

        results = sorted(
            results,
            key=lambda x: x["score"],
            reverse=True
        )[:15]

        sector_count = (
            pd.Series([r["sector"] for r in results])
            .value_counts()
        )

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

    print(msg)

    # ======================================
    # DISCORD
    # ======================================

    try:

        requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": msg},
            timeout=10
        )

        print("Sent to Discord")

    except Exception as e:

        print("Discord error:", e)


# ==========================================
# RUN
# ==========================================
if __name__ == "__main__":
    main()
