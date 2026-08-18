import asyncio
import inspect
import os
import time
from collections import deque

from flask import Flask, jsonify, render_template, request
from pyquotex.stable_api import Quotex

app = Flask(__name__)

UA = os.getenv(
    "CORTEX_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
)

TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900}
EXPIRIES = {"1": 60, "5": 300, "15": 900}
ALLOWED_ASSETS = {
    "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "EURJPY_otc",
    "AUDCAD_otc", "CADJPY_otc", "USDCHF_otc", "GBPJPY_otc",
}


async def close_quiet(client):
    try:
        result = client.close()
        if inspect.isawaitable(result):
            await result
    except Exception:
        pass


def event_name(raw):
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    text = str(raw)
    for name in (
        "authorization/reject",
        "s_authorization",
        "history/load",
        "history/list/v2",
        "instruments/update",
        "instruments/get",
        "instruments/list",
        "candle-generated",
        "assets_list",
        "candles",
        "depth/follow",
        "tick",
    ):
        if name in text:
            return name
    return "socketio_frame" if text.startswith(("0", "40", "41", "42", "45", "50", "51")) else "other"


async def fetch(ssid, asset, period):
    client = Quotex(
        email="ssid-only@local.invalid",
        password="unused",
        lang="en",
        root_path="/tmp/cortex-quotex",
        user_data_dir="/tmp/cortex-browser",
        asset_default=asset,
        period_default=period,
    )
    client.session_data = {"token": ssid, "cookies": "", "user_agent": UA}

    sent = deque(maxlen=60)
    received = deque(maxlen=60)

    try:
        connected, reason = await client.connect()
        if not connected:
            return {
                "ok": False,
                "reason": reason,
                "transport": {"sent": list(sent), "received": list(received)},
            }

        api = client.api
        original_send = api.send_websocket_request
        original_receive = api._on_message

        async def send(data):
            sent.append({
                "direction": "send",
                "event": event_name(data),
                "ts": time.time(),
            })
            return await original_send(data)

        async def receive(data):
            received.append({
                "direction": "recv",
                "event": event_name(data),
                "ts": time.time(),
            })
            return await original_receive(data)

        api.send_websocket_request = send
        api._on_message = receive

        # The live Quotex history endpoint may return fewer rows than requested.
        # Keep the diagnostic truthful and analyze the rows that were actually verified.
        candles = await client.get_candles(
            asset,
            time.time(),
            period * 50,
            period,
            timeout=10,
            use_cache=False,
        )

        return {
            "ok": True,
            "candles": candles or [],
            "transport": {"sent": list(sent), "received": list(received)},
        }
    finally:
        await close_quiet(client)


def _number(series):
    import pandas as pd
    return pd.to_numeric(series, errors="coerce")


def analyze_candles(candles):
    """Read-only indicator score.

    The previous version required 60 candles, but the verified Quotex history
    response currently supplies about 8-10 candles. That made every successful
    transport request end in NO SIGNAL even though candles were present.

    This version deliberately uses indicators that can work with 10+ candles:
    EMA(5), RSI(7), Bollinger(10), Stochastic(5), and short-term momentum.
    It never fabricates candles.
    """
    import pandas as pd

    if len(candles) < 5:
        return {
            "signal": "NO SIGNAL",
            "confidence": 0,
            "reason": f"Only {len(candles)} verified candles received; at least 5 are required for the basic model.",
            "candles_used": len(candles),
        }

    df = pd.DataFrame(candles).copy()
    for column in ("open", "high", "low", "close"):
        if column not in df.columns:
            return {
                "signal": "NO SIGNAL",
                "confidence": 0,
                "reason": f"Candle payload is missing '{column}'.",
                "candles_used": len(df),
            }
        df[column] = _number(df[column])

    if "time" in df.columns:
        df["time"] = _number(df["time"])
        df = df.sort_values("time")

    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(df) < 5:
        return {
            "signal": "NO SIGNAL",
            "confidence": 0,
            "reason": f"Only {len(df)} usable candles remain after validation; at least 5 are required.",
            "candles_used": len(df),
        }

    close = df["close"]
    high = df["high"]
    low = df["low"]

    n = len(df)
    rsi_period = min(7, max(3, n - 1))
    bb_period = min(10, n)
    stoch_period = min(5, n)
    momentum_period = min(3, n - 1)

    # EMA(5)
    ema5 = close.ewm(span=5, adjust=False).mean()

    # RSI (adaptive for short verified history)
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / rsi_period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / rsi_period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi7 = 100 - (100 / (1 + rs))

    # Bollinger (adaptive for short verified history)
    mid = close.rolling(bb_period).mean()
    sd = close.rolling(bb_period).std(ddof=0)
    upper = mid + 2 * sd
    lower = mid - 2 * sd

    # Stochastic (adaptive for short verified history)
    low5 = low.rolling(stoch_period).min()
    high5 = high.rolling(stoch_period).max()
    stoch5 = 100 * (close - low5) / (high5 - low5).replace(0, float("nan"))

    # Very short-term momentum
    momentum3 = close.diff(momentum_period)

    latest = {
        "close": float(close.iloc[-1]),
        "ema5": float(ema5.iloc[-1]),
        "rsi7": float(rsi7.iloc[-1]),
        "bb_upper": float(upper.iloc[-1]),
        "bb_lower": float(lower.iloc[-1]),
        "stoch5": float(stoch5.iloc[-1]),
        "momentum3": float(momentum3.iloc[-1]),
    }

    votes = []

    # Trend
    votes.append("CALL" if latest["close"] > latest["ema5"] else "PUT")

    # RSI
    if latest["rsi7"] >= 55:
        votes.append("CALL")
    elif latest["rsi7"] <= 45:
        votes.append("PUT")
    else:
        votes.append("NEUTRAL")

    # Bollinger mean-reversion / breakout guard
    if latest["close"] < latest["bb_lower"]:
        votes.append("CALL")
    elif latest["close"] > latest["bb_upper"]:
        votes.append("PUT")
    else:
        votes.append("NEUTRAL")

    # Stochastic
    if latest["stoch5"] >= 60:
        votes.append("CALL")
    elif latest["stoch5"] <= 40:
        votes.append("PUT")
    else:
        votes.append("NEUTRAL")

    # Momentum
    votes.append("CALL" if latest["momentum3"] > 0 else "PUT" if latest["momentum3"] < 0 else "NEUTRAL")

    call_votes = votes.count("CALL")
    put_votes = votes.count("PUT")
    decisive = max(call_votes, put_votes)

    if call_votes >= 3 and call_votes > put_votes:
        signal = "CALL"
    elif put_votes >= 3 and put_votes > call_votes:
        signal = "PUT"
    else:
        signal = "NO SIGNAL"

    confidence = decisive * 20 if signal != "NO SIGNAL" else max(call_votes, put_votes) * 20

    return {
        "signal": signal,
        "confidence": confidence,
        "votes": votes,
        "indicators": latest,
        "candles_used": len(df),
        "model": f"EMA5 + RSI{rsi_period} + Bollinger{bb_period} + Stochastic{stoch_period} + Momentum{momentum_period}",
        "note": "Read-only indicator score. No order/trade action is performed and this is not a guarantee of outcome.",
    }


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    return jsonify(ok=True, service="cortex-quotex-mvp")


@app.post("/api/analyze")
def api_analyze():
    data = request.get_json(silent=True) or {}
    ssid = str(data.get("ssid", "")).strip()
    asset = str(data.get("asset", "EURUSD_otc")).strip()
    timeframe = str(data.get("timeframe", "1m")).strip()
    expiry = str(data.get("expiry", "1")).strip()

    if not ssid:
        return jsonify(ok=False, error="SSID is required"), 400
    if asset not in ALLOWED_ASSETS:
        return jsonify(ok=False, error="Invalid asset"), 400
    if timeframe not in TIMEFRAMES:
        return jsonify(ok=False, error="Invalid timeframe"), 400
    if expiry not in EXPIRIES:
        return jsonify(ok=False, error="Invalid expiry"), 400

    started = time.time()

    try:
        result = asyncio.run(fetch(ssid, asset, TIMEFRAMES[timeframe]))
    except Exception as exc:
        return jsonify(
            ok=False,
            stage="quotex_connection_or_candle_fetch",
            error=type(exc).__name__,
            message=str(exc),
            elapsed_seconds=round(time.time() - started, 3),
        ), 502

    if not result["ok"]:
        return jsonify(
            ok=False,
            asset=asset,
            timeframe=timeframe,
            expiry=expiry,
            elapsed_seconds=round(time.time() - started, 3),
            diagnostics=result,
            note="Diagnostic only. No order/trade action is performed.",
        ), 502

    candles = result["candles"]
    transport = result["transport"]
    diagnostic = {
        "candle_count": len(candles),
        "history_request_sent": any(x["event"] == "history/load" for x in transport["sent"]),
        "history_response_observed": any(
            x["event"] in ("history/load", "history/list/v2", "candles")
            for x in transport["received"]
        ),
        "transport": transport,
    }

    if not candles:
        return jsonify(
            ok=True,
            signal="NO SIGNAL",
            asset=asset,
            timeframe=timeframe,
            expiry=expiry,
            elapsed_seconds=round(time.time() - started, 3),
            diagnostics=diagnostic,
            note="Connected, but no verified candles returned. No fake signal generated.",
        )

    analysis = analyze_candles(candles)
    return jsonify(
        ok=True,
        asset=asset,
        timeframe=timeframe,
        expiry=expiry,
        elapsed_seconds=round(time.time() - started, 3),
        signal=analysis["signal"],
        confidence=analysis["confidence"],
        analysis=analysis,
        diagnostics=diagnostic,
        note="Read-only. No order/trade action is performed.",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
