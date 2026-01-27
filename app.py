import streamlit as st
status = "🚀 TRIGGER" if c.iloc[i] > range_high else "🟡 SETUP"
score_norm = round(score / 11 * 100, 2)


return status, score_norm, atr14.iloc[i], range_low, c.iloc[i]


status_y, score_y, *_ = compute(-2)
status_t, score_t, atr, range_low, close = compute(-1)


if status_t == "🚀 TRIGGER" and status_y != "🚀 TRIGGER" and score_t >= MIN_SCORE:
return {
"Status": status_t,
"Score": score_t,
"ATR": atr,
"RangeLow": range_low,
"DailyClose": close
}


return None


# =====================================================
# DISCORD — TRIGGERS SEULEMENT
# =====================================================
def send_to_discord(df):
if not DISCORD_WEBHOOK or df.empty:
return


lines = []
for _, r in df.iterrows():
lines.append(
f"🚀 **{r['Ticker']}** @ ${r['Price']} | "
f"Score `{r['Score']}` | "
f"R:R `{r['R:R']}` | "
f"SL `{r['SL']}` → TP `{r['TP1']}`"
)


payload = {
"content": "🚨 **NOUVEAUX TRIGGERS — Modèle 3**\n\n" + "\n".join(lines[:20])
}


try:
requests.post(DISCORD_WEBHOOK, json=payload, timeout=5)
except Exception:
pass


# =====================================================
# UI
# =====================================================
st.title("🚨 Modèle 3 — Discord = nouveaux triggers uniquement")


limit = st.slider("Nombre de tickers à analyser", 50, len(TICKERS), 300)


if st.button("🚀 Scanner et envoyer sur Discord"):
rows = []


with st.spinner("Scan en cours…"):
for t in TICKERS[:limit]:
df = get_ohlc(t)
if df is None:
continue


m3 = model3_setup(df)
if not m3:
continue


price = get_market_price(t)
if price is None:
price = round(m3["DailyClose"], 2)


atr = m3["ATR"]
sl = round(m3["RangeLow"] - 0.2 * atr + (price - m3["DailyClose"]), 2)


tp1 = round(price + 2 * atr, 2)
tp2 = round(price + 3 * atr, 2)


risk = price - sl
rr = round((tp1 - price) / risk, 2) if risk > 0 else None


if rr is None or rr < MIN_RR:
continue


rows.append([
t, price, m3["Score"], sl, tp1, tp2, rr
])


if rows:
result = pd.DataFrame(rows, columns=[
"Ticker", "Price", "Score", "SL", "TP1", "TP2", "R:R"
]).sort_values("Score", ascending=False)


st.dataframe(result, use_container_width=True)
send_to_discord(result)
st.success("🚀 Nouveaux triggers envoyés sur Discord")
else:
st.info("Aucun nouveau trigger aujourd’hui.")
