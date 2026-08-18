import os
from flask import Flask, jsonify, render_template, request
app=Flask(__name__)
SESSION={"connected":False,"ssid":""}

@app.get("/")
def home(): return render_template("index.html")

@app.post("/api/session")
def session():
    d=request.get_json(silent=True) or {}
    ssid=str(d.get("ssid","")).strip()
    if not ssid: return jsonify(ok=False,error="SSID is required"),400
    SESSION.update(connected=True,ssid=ssid)
    return jsonify(ok=True,connected=True,message="SSID saved for this server session")

@app.get("/api/session/status")
def status(): return jsonify(ok=True,connected=SESSION["connected"])

@app.post("/api/analyze")
def analyze():
    d=request.get_json(silent=True) or {}
    if not SESSION["connected"]: return jsonify(ok=False,error="Connect with SSID first"),400
    return jsonify(ok=False,code="CANDLE_DATA_REQUIRED",
        message="Connected, but no verified candle data is available. No fake CALL/PUT signal was generated.",
        asset=d.get("asset"),timeframe=d.get("timeframe"),expiry=d.get("expiry")),503

@app.get("/healthz")
def healthz(): return jsonify(ok=True)

if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)))
