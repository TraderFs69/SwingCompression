"""TEA Breakout - scanner Russell 3000 avec Yahoo Finance.

Le fichier ``russell3000_constituents.xlsx`` doit etre place dans le meme
dossier. Il doit contenir une colonne ``Symbol`` (ou une colonne dont le nom
contient "sym"). Une colonne ``Sector`` est utilisee lorsqu'elle existe.

Le scanner distingue une approche de resistance d'une cassure confirmee par
la cloture. Il recherche une tendance propre, une base suffisamment compacte,
plusieurs tests de resistance et une force relative favorable contre SPY.

Variable d'environnement requise :
    DISCORD_WEBHOOK_URL
"""

from __future__ import annotations

import calendar
import os
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# =====================================================
# CONFIGURATION
# =====================================================

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

CONSTITUENTS_FILE = Path("russell3000_constituents.xlsx")
OUTPUT_CSV = Path("tea_breakout_resultats.csv")

LOOKBACK_DAYS = 550
RESISTANCE_LOOKBACK_BARS = 20
TOUCH_LOOKBACK_BARS = 60
TOUCH_TOLERANCE = 2.0
MIN_TOUCH_SPACING_BARS = 3
MIN_RESISTANCE_TOUCHES = 2

BREAKOUT_BUFFER = 0.10
MAX_BREAKOUT_EXTENSION = 5.0
MAX_EARLY_DISTANCE = 2.0
MAX_EMA20_EXTENSION = 10.0
MAX_BASE_RANGE = 25.0

RELATIVE_STRENGTH_BARS = 63
MIN_RELATIVE_STRENGTH = 0.0

MIN_PRICE = 2.0
MIN_AVG_DOLLAR_VOLUME = 5_000_000
MIN_RISK_REWARD = 1.50
MIN_SCORE = 55.0

TOP_N = 15
REQUEST_SLEEP = 0.25
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 30
DISCORD_MAX_LENGTH = 1900


# =====================================================
# VALIDATION ET SESSION HTTP
# =====================================================

if not DISCORD_WEBHOOK_URL:
    raise ValueError("DISCORD_WEBHOOK_URL manquant")


def build_session() -> requests.Session:
    session = requests.Session()

    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )

    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0 Safari/537.36"
            ),
            "Accept-Encoding": "gzip",
            "Accept": "application/json,text/plain,*/*",
        }
    )
    return session


SESSION = build_session()


# =====================================================
# UNIVERS RUSSELL 3000
# =====================================================

def load_universe() -> pd.DataFrame:
    if not CONSTITUENTS_FILE.exists():
        raise FileNotFoundError(f"Fichier introuvable : {CONSTITUENTS_FILE}")

    df = pd.read_excel(CONSTITUENTS_FILE)

    if "Symbol" not in df.columns:
        candidates = [column for column in df.columns if "sym" in str(column).lower()]
        if candidates:
            df = df.rename(columns={candidates[0]: "Symbol"})
        else:
            df = df.rename(columns={df.columns[0]: "Symbol"})

    if "Sector" not in df.columns:
        sector_candidates = [
            column for column in df.columns
            if "sector" in str(column).lower() or "secteur" in str(column).lower()
        ]
        if sector_candidates:
            df = df.rename(columns={sector_candidates[0]: "Sector"})
        else:
            df["Sector"] = "N/A"

    df["Symbol"] = (
        df["Symbol"]
        .astype(str)
        .str.strip()
        .str.upper()
        .str.replace("/", ".", regex=False)
    )
    df["Sector"] = df["Sector"].fillna("N/A").astype(str).str.strip()

    df = df[
        df["Symbol"].notna()
        & df["Symbol"].ne("")
        & df["Symbol"].ne("NAN")
        & df["Symbol"].ne("SYMBOL")
    ]
    df = df.drop_duplicates(subset=["Symbol"], keep="first")

    print(f"{len(df)} symboles charges depuis {CONSTITUENTS_FILE}")
    return df[["Symbol", "Sector"]].reset_index(drop=True)


# =====================================================
# DONNEES YAHOO FINANCE
# =====================================================

def to_yahoo_symbol(ticker: str) -> str:
    return ticker.replace("/", "-").replace(".", "-")


def get_daily_data(ticker: str, start: date, end: date) -> pd.DataFrame | None:
    yahoo_symbol = to_yahoo_symbol(ticker)
    encoded_symbol = quote(yahoo_symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}"

    params = {
        "period1": calendar.timegm(start.timetuple()),
        "period2": calendar.timegm((end + timedelta(days=1)).timetuple()),
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }

    try:
        response = SESSION.get(
            url,
            params=params,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )

        if response.status_code != 200:
            print(f"{ticker}: Yahoo Finance HTTP {response.status_code}")
            return None

        payload = response.json()
        chart = payload.get("chart", {})

        if chart.get("error"):
            print(f"{ticker}: Yahoo Finance - {chart['error']}")
            return None

        results = chart.get("result") or []
        if not results:
            return None

        result = results[0]
        timestamps = result.get("timestamp") or []
        indicators = result.get("indicators", {})
        quote_blocks = indicators.get("quote") or []
        adjusted_blocks = indicators.get("adjclose") or []

        if not timestamps or not quote_blocks:
            return None

        market_data = quote_blocks[0]
        raw_close = market_data.get("close") or []
        adjusted_close = (
            adjusted_blocks[0].get("adjclose", [])
            if adjusted_blocks else []
        )

        required = {
            "Open": market_data.get("open") or [],
            "High": market_data.get("high") or [],
            "Low": market_data.get("low") or [],
            "RawClose": raw_close,
            "Volume": market_data.get("volume") or [],
        }

        expected_length = len(timestamps)
        if any(len(values) != expected_length for values in required.values()):
            return None

        if len(adjusted_close) != expected_length:
            adjusted_close = raw_close

        dates = pd.to_datetime(
            timestamps,
            unit="s",
            utc=True,
        ).tz_convert(None).normalize()

        df = pd.DataFrame(required, index=dates)
        df["AdjustedClose"] = adjusted_close

        numeric_columns = [
            "Open",
            "High",
            "Low",
            "RawClose",
            "AdjustedClose",
            "Volume",
        ]
        for column in numeric_columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

        df = df.dropna(
            subset=["Open", "High", "Low", "RawClose", "AdjustedClose", "Volume"]
        )
        df = df[df["RawClose"] > 0]

        if df.empty:
            return None

        adjustment_factor = df["AdjustedClose"] / df["RawClose"]
        df["Open"] = df["Open"] * adjustment_factor
        df["High"] = df["High"] * adjustment_factor
        df["Low"] = df["Low"] * adjustment_factor
        df["Close"] = df["AdjustedClose"]

        df = df[["Open", "High", "Low", "Close", "Volume"]]
        df = df[~df.index.duplicated(keep="last")].sort_index()

        # La bougie du jour peut etre encore en formation.
        if not df.empty and df.index[-1].date() >= date.today():
            df = df.iloc[:-1]

        return df if not df.empty else None

    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        print(f"{ticker}: erreur Yahoo Finance - {exc}")
        return None


# =====================================================
# INDICATEURS
# =====================================================

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()

    result["EMA20"] = result["Close"].ewm(span=20, adjust=False).mean()
    result["EMA50"] = result["Close"].ewm(span=50, adjust=False).mean()
    result["EMA200"] = result["Close"].ewm(span=200, adjust=False).mean()

    previous_close = result["Close"].shift(1)
    true_range = pd.concat(
        [
            result["High"] - result["Low"],
            (result["High"] - previous_close).abs(),
            (result["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result["ATR14"] = true_range.rolling(14).mean()
    result["VolumeMA20"] = result["Volume"].rolling(20).mean()
    result["AvgDollarVolume20"] = (
        (result["Close"] * result["Volume"])
        .rolling(20)
        .mean()
    )

    return result


def count_resistance_touches(
    df: pd.DataFrame,
    resistance: float,
) -> int:
    prior_highs = df["High"].shift(1).tail(TOUCH_LOOKBACK_BARS)
    lower = resistance * (1 - TOUCH_TOLERANCE / 100)
    upper = resistance * (1 + TOUCH_TOLERANCE / 100)

    near_positions = [
        position
        for position, value in enumerate(prior_highs.tolist())
        if pd.notna(value) and lower <= float(value) <= upper
    ]

    touches = 0
    last_counted_position: int | None = None

    for position in near_positions:
        if (
            last_counted_position is None
            or position - last_counted_position >= MIN_TOUCH_SPACING_BARS
        ):
            touches += 1
            last_counted_position = position

    return touches


def relative_strength_vs_spy(
    stock: pd.DataFrame,
    spy: pd.DataFrame,
) -> dict | None:
    comparison = pd.concat(
        [
            stock["Close"].rename("stock"),
            spy["Close"].rename("spy"),
        ],
        axis=1,
        join="inner",
    ).dropna()

    required_rows = RELATIVE_STRENGTH_BARS + 1
    if len(comparison) < required_rows:
        return None

    comparison = comparison.tail(required_rows)
    stock_return = (comparison["stock"].iloc[-1] / comparison["stock"].iloc[0] - 1) * 100
    spy_return = (comparison["spy"].iloc[-1] / comparison["spy"].iloc[0] - 1) * 100

    return {
        "stock_return": float(stock_return),
        "spy_return": float(spy_return),
        "relative_strength": float(stock_return - spy_return),
    }


# =====================================================
# SIGNAL, PLAN ET SCORE
# =====================================================

def classify_breakout(close: float, resistance: float) -> dict | None:
    position_pct = (close / resistance - 1) * 100

    if BREAKOUT_BUFFER <= position_pct <= MAX_BREAKOUT_EXTENSION:
        return {
            "type": "BREAKOUT CONFIRME",
            "position_pct": position_pct,
        }

    if -MAX_EARLY_DISTANCE <= position_pct < BREAKOUT_BUFFER:
        return {
            "type": "APPROCHE",
            "position_pct": position_pct,
        }

    return None


def build_trade_plan(
    setup_type: str,
    close: float,
    resistance: float,
    base_low: float,
    ema20: float,
    recent_low: float,
    atr: float,
) -> dict | None:
    possible_supports = [ema20, recent_low]

    if setup_type == "BREAKOUT CONFIRME":
        possible_supports.append(resistance)

    supports_below_price = [level for level in possible_supports if level < close]
    if not supports_below_price or atr <= 0:
        return None

    support_reference = max(supports_below_price)
    stop = support_reference - 0.50 * atr

    # Objectif par mouvement mesure de la base de 20 seances.
    target = resistance + (resistance - base_low)
    risk = close - stop
    reward = target - close

    if risk <= 0 or reward <= 0:
        return None

    return {
        "stop": stop,
        "target": target,
        "risk_reward": reward / risk,
    }


def compute_score(
    setup_type: str,
    position_pct: float,
    touches: int,
    relative_strength: float,
    volume_ratio: float,
    ema20_extension: float,
    risk_reward: float,
) -> float:
    score = 0.0

    # Qualite du signal : 25 points.
    if setup_type == "BREAKOUT CONFIRME":
        score += 25.0
    else:
        proximity = abs(position_pct)
        score += max(15.0, 25.0 * (1 - proximity / MAX_EARLY_DISTANCE))

    # Structure EMA20 > EMA50 > EMA200 deja obligatoire : 15 points.
    score += 15.0

    # Nombre de tests distincts de la resistance : 15 points.
    score += min(15.0, 10.0 + max(0, touches - 2) * 2.5)

    # Force relative contre SPY : 15 points.
    score += min(15.0, max(0.0, relative_strength) * 1.5)

    # Volume relatif : composante du score seulement, jamais filtre obligatoire.
    volume_score = (volume_ratio - 0.80) / 1.20 * 10.0
    score += min(10.0, max(0.0, volume_score))

    # Evite de poursuivre un titre deja trop etire au-dessus de l'EMA20.
    score += max(
        0.0,
        10.0 * (1 - ema20_extension / MAX_EMA20_EXTENSION),
    )

    # Ratio rendement/risque : 10 points.
    score += min(10.0, max(0.0, risk_reward) * 4.0)

    return round(min(100.0, score), 1)


# =====================================================
# ANALYSE D'UN TITRE
# =====================================================

def analyze_stock(
    ticker: str,
    sector: str,
    df: pd.DataFrame,
    spy: pd.DataFrame,
) -> dict | None:
    if len(df) < 220:
        return None

    df = add_indicators(df)
    last = df.iloc[-1]
    previous = df.iloc[-2]

    close = float(last["Close"])
    ema20 = float(last["EMA20"])
    ema50 = float(last["EMA50"])
    ema200 = float(last["EMA200"])
    atr = float(last["ATR14"])
    volume = float(last["Volume"])
    volume_ma20 = float(last["VolumeMA20"])
    avg_dollar_volume = float(last["AvgDollarVolume20"])

    required_values = [
        close,
        ema20,
        ema50,
        ema200,
        atr,
        volume,
        volume_ma20,
        avg_dollar_volume,
    ]
    if any(pd.isna(value) for value in required_values):
        return None

    if close < MIN_PRICE or avg_dollar_volume < MIN_AVG_DOLLAR_VOLUME:
        return None

    if not (close > ema20 > ema50 > ema200):
        return None

    # Une cassure ou une approche doit montrer une cloture en progression.
    if close <= float(previous["Close"]):
        return None

    ema20_extension = (close / ema20 - 1) * 100
    if ema20_extension < 0 or ema20_extension > MAX_EMA20_EXTENSION:
        return None

    resistance = float(
        df["High"]
        .shift(1)
        .rolling(RESISTANCE_LOOKBACK_BARS)
        .max()
        .iloc[-1]
    )
    base_low = float(
        df["Low"]
        .shift(1)
        .rolling(RESISTANCE_LOOKBACK_BARS)
        .min()
        .iloc[-1]
    )

    if pd.isna(resistance) or pd.isna(base_low) or base_low <= 0:
        return None

    base_range = (resistance - base_low) / base_low * 100
    if base_range > MAX_BASE_RANGE:
        return None

    setup = classify_breakout(close, resistance)
    if setup is None:
        return None

    touches = count_resistance_touches(df, resistance)
    if touches < MIN_RESISTANCE_TOUCHES:
        return None

    strength = relative_strength_vs_spy(df, spy)
    if strength is None:
        return None

    if strength["relative_strength"] < MIN_RELATIVE_STRENGTH:
        return None

    recent_low = float(df["Low"].tail(10).min())
    plan = build_trade_plan(
        setup_type=setup["type"],
        close=close,
        resistance=resistance,
        base_low=base_low,
        ema20=ema20,
        recent_low=recent_low,
        atr=atr,
    )

    if plan is None or plan["risk_reward"] < MIN_RISK_REWARD:
        return None

    volume_ratio = volume / volume_ma20 if volume_ma20 > 0 else 0.0
    score = compute_score(
        setup_type=setup["type"],
        position_pct=setup["position_pct"],
        touches=touches,
        relative_strength=strength["relative_strength"],
        volume_ratio=volume_ratio,
        ema20_extension=ema20_extension,
        risk_reward=plan["risk_reward"],
    )

    if score < MIN_SCORE:
        return None

    reasons = [
        "EMA20 > EMA50 > EMA200",
        f"Resistance testee {touches} fois",
        f"RS vs SPY {strength['relative_strength']:+.2f} pts",
        f"Vol x{volume_ratio:.2f}",
        f"R/R {plan['risk_reward']:.2f}",
    ]

    if setup["type"] == "BREAKOUT CONFIRME":
        reasons.insert(0, "Cloture au-dessus de la resistance")
    else:
        reasons.insert(0, "Cloture proche de la resistance")

    if close > float(last["Open"]):
        reasons.append("Bougie haussiere")

    return {
        "Ticker": ticker,
        "Sector": sector,
        "Type": setup["type"],
        "Price": round(close, 2),
        "Score": score,
        "Resistance": round(resistance, 2),
        "Position vs resistance %": round(setup["position_pct"], 2),
        "Resistance touches": touches,
        "Base range %": round(base_range, 2),
        "EMA20 extension %": round(ema20_extension, 2),
        "Volume ratio": round(volume_ratio, 2),
        "Stock return 3m %": round(strength["stock_return"], 2),
        "SPY return 3m %": round(strength["spy_return"], 2),
        "Relative strength": round(strength["relative_strength"], 2),
        "Stop": round(plan["stop"], 2),
        "Target": round(plan["target"], 2),
        "Risk/Reward": round(plan["risk_reward"], 2),
        "Reasons": " | ".join(reasons),
    }


# =====================================================
# DISCORD
# =====================================================

def split_message(message: str, limit: int = DISCORD_MAX_LENGTH) -> list[str]:
    if len(message) <= limit:
        return [message]

    chunks: list[str] = []
    current = ""

    for block in message.split("\n\n"):
        candidate = block if not current else f"{current}\n\n{block}"

        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            chunks.append(current)

        if len(block) <= limit:
            current = block
        else:
            chunks.append(block[:limit])
            current = block[limit:]

    if current:
        chunks.append(current)

    return chunks


def send_discord(message: str) -> None:
    for chunk in split_message(message):
        try:
            response = SESSION.post(
                DISCORD_WEBHOOK_URL,
                json={"content": chunk},
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )

            if response.status_code not in (200, 204):
                print(
                    f"Discord HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                )
            else:
                print("Message Discord envoye")

        except requests.RequestException as exc:
            print(f"Erreur Discord : {exc}")


def build_report(results: list[dict], analysed: int) -> str:
    if not results:
        return (
            "🚀 **TEA BREAKOUT - RUSSELL 3000**\n\n"
            f"{analysed} titres analyses.\n"
            "Aucune approche ni cassure ne respecte toutes les confirmations aujourd'hui."
        )

    sector_count = pd.Series([result["Sector"] for result in results]).value_counts()
    confirmed = sum(result["Type"] == "BREAKOUT CONFIRME" for result in results)
    approaches = len(results) - confirmed

    dominant_sectors = " | ".join(
        f"{sector}: {count}"
        for sector, count in sector_count.head(3).items()
    )

    sections = [
        "🚀 **TEA BREAKOUT - RUSSELL 3000**\n"
        f"{analysed} titres analyses | Confirmes: {confirmed} | Approches: {approaches}\n"
        f"Secteurs dominants: {dominant_sectors}"
    ]
    medals = ["🥇", "🥈", "🥉"]

    for index, result in enumerate(results):
        marker = medals[index] if index < 3 else "📈"
        sections.append(
            f"{marker} **{result['Ticker']}** | {result['Sector']} | "
            f"{result['Type']} | Score {result['Score']}/100\n"
            f"Prix ${result['Price']} | Resistance ${result['Resistance']} | "
            f"RS {result['Relative strength']:+.2f} pts | Vol x{result['Volume ratio']}\n"
            f"Stop ${result['Stop']} | Cible ${result['Target']} | "
            f"R/R {result['Risk/Reward']}"
        )

    sections.append(
        "Une approche n'est pas encore une cassure; attendre une confirmation si necessaire."
    )
    return "\n\n".join(sections)


# =====================================================
# PROGRAMME PRINCIPAL
# =====================================================

def main() -> None:
    print("Demarrage de TEA Breakout - Yahoo Finance")

    universe = load_universe()
    total = len(universe)

    end = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)

    print("Telechargement de SPY pour la force relative")
    spy = get_daily_data("SPY", start, end)
    if spy is None or len(spy) < 220:
        raise RuntimeError("Impossible de charger suffisamment de donnees pour SPY")

    candidates: list[dict] = []
    valid_histories = 0

    for index, row in universe.iterrows():
        ticker = row["Symbol"]
        sector = row["Sector"]
        position = index + 1

        print(f"{position}/{total} - {ticker}")

        df = get_daily_data(ticker, start, end)
        time.sleep(REQUEST_SLEEP)

        if df is None:
            continue

        valid_histories += 1
        result = analyze_stock(ticker, sector, df, spy)

        if result is not None:
            candidates.append(result)

    candidates = sorted(
        candidates,
        key=lambda item: (
            item["Type"] == "BREAKOUT CONFIRME",
            item["Score"],
            item["Relative strength"],
            item["Risk/Reward"],
        ),
        reverse=True,
    )[:TOP_N]

    if candidates:
        pd.DataFrame(candidates).to_csv(
            OUTPUT_CSV,
            index=False,
            encoding="utf-8-sig",
        )
        print(f"Resultats enregistres dans {OUTPUT_CSV}")

    report = build_report(candidates, valid_histories)
    print("\n" + report + "\n")
    send_discord(report)

    print(
        f"Scan termine | {valid_histories}/{total} historiques valides | "
        f"{len(candidates)} candidat(s)"
    )


if __name__ == "__main__":
    main()
