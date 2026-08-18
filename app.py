import asyncio, inspect, os, time
from collections import deque
from flask import Flask, jsonify, render_template, request
from pyquotex.stable_api import Quotex
app=Flask(__name__)
UA=os.getenv('CORTEX_USER_AGENT','Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36')
TIMEFRAMES={'1m':60,'5m':300,'15m':900}
EXPIRIES={'1':60,'5':300,'15':900}
ALLOWED_ASSETS={'EURUSD_otc','GBPUSD_otc','USDJPY_otc','EURJPY_otc','AUDCAD_otc','CADJPY_otc','USDCHF_otc','GBPJPY_otc'}
async def close_quiet(c):
    try:
        r=c.close()
        if inspect.isawaitable(r): await r
    except Exception: pass
def event_name(raw):
    if isinstance(raw,bytes): raw=raw.decode('utf-8','ignore')
    s=str(raw)
    for n in ('authorization/reject','s_authorization','history/load','history/list/v2','instruments/update','instruments/get','instruments/list','candle-generated','assets_list','candles','depth/follow','tick'):
        if n in s: return n
    return 'socketio_frame' if s.startswith(('0','40','41','42','45','50','51')) else 'other'
async def fetch(ssid,asset,period):
    c=Quotex(email='ssid-only@local.invalid',password='unused',lang='en',root_path='/tmp/cortex-quotex',user_data_dir='/tmp/cortex-browser',asset_default=asset,period_default=period)
    c.session_data={'token':ssid,'cookies':'','user_agent':UA}
    sent,recv=deque(maxlen=40),deque(maxlen=40)
    try:
        ok,reason=await c.connect()
        if not ok:
            return {'ok':False,'reason':reason,'transport':{'sent':list(sent),'received':list(recv)}}
        api=c.api
        send0=api.send_websocket_request; recv0=api._on_message
        async def send(data):
            sent.append({'direction':'send','event':event_name(data),'ts':time.time()}); return await send0(data)
        async def receive(data):
            recv.append({'direction':'recv','event':event_name(data),'ts':time.time()}); return await recv0(data)
        api.send_websocket_request=send; api._on_message=receive
        candles=await c.get_candles(asset,time.time(),period*50,period,timeout=10,use_cache=False)
        return {'ok':True,'candles':candles or [],'transport':{'sent':list(sent),'received':list(recv)}}
    finally: await close_quiet(c)
def analyze_candles(candles):
    import pandas as pd
    if len(candles)<60: return {'signal':'NO SIGNAL','confidence':0,'reason':f'{len(candles)} candles received; 60 required.'}
    df=pd.DataFrame(candles)
    for x in ('open','high','low','close'): df[x]=pd.to_numeric(df[x],errors='coerce')
    df=df.dropna(subset=['open','high','low','close']).sort_values('time'); close=df.close; high=df.high; low=df.low
    ema=close.ewm(span=20,adjust=False).mean(); d=close.diff(); gain=d.clip(lower=0).ewm(alpha=1/14,adjust=False).mean(); loss=(-d.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean(); rsi=100-100/(1+gain/loss.replace(0,float('nan')))
    mid=close.rolling(20).mean(); sd=close.rolling(20).std(); upper=mid+2*sd; lower=mid-2*sd
    lo=low.rolling(14).min(); hi=high.rolling(14).max(); stoch=100*(close-lo)/(hi-lo).replace(0,float('nan'))
    tr=pd.concat([high-low,(high-close.shift()).abs(),(low-close.shift()).abs()],axis=1).max(axis=1); atr=tr.ewm(alpha=1/14,adjust=False).mean(); up=high.diff(); dn=-low.diff(); p=up.where((up>dn)&(up>0),0); m=dn.where((dn>up)&(dn>0),0); pdi=100*p.ewm(alpha=1/14,adjust=False).mean()/atr.replace(0,float('nan')); mdi=100*m.ewm(alpha=1/14,adjust=False).mean()/atr.replace(0,float('nan')); dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,float('nan')); adx=dx.ewm(alpha=1/14,adjust=False).mean()
    v={'close':float(close.iloc[-1]),'ema20':float(ema.iloc[-1]),'rsi14':float(rsi.iloc[-1]),'bb_upper':float(upper.iloc[-1]),'bb_lower':float(lower.iloc[-1]),'stoch_k':float(stoch.iloc[-1]),'adx14':float(adx.iloc[-1])}
    votes=[('CALL' if v['close']>v['ema20'] else 'PUT'),('CALL' if v['rsi14']>55 else 'PUT' if v['rsi14']<45 else 'NEUTRAL'),('PUT' if v['close']>v['bb_upper'] else 'CALL' if v['close']<v['bb_lower'] else 'NEUTRAL'),('CALL' if v['stoch_k']>55 else 'PUT' if v['stoch_k']<45 else 'NEUTRAL'),('CALL' if v['adx14']>=20 and v['close']>v['ema20'] else 'PUT' if v['adx14']>=20 and v['close']<v['ema20'] else 'NEUTRAL')]
    call,put=votes.count('CALL'),votes.count('PUT')
    signal='CALL' if call>=4 else 'PUT' if put>=4 else 'NO SIGNAL'; conf=(max(call,put)*20 if signal=='NO SIGNAL' else (call if signal=='CALL' else put)*20)
    return {'signal':signal,'confidence':conf,'votes':votes,'indicators':v,'candles_used':len(df),'note':'Indicator score only; not a guarantee of outcome.'}
@app.get('/')
def home(): return render_template('index.html')
@app.get('/healthz')
def healthz(): return jsonify(ok=True,service='cortex-quotex-mvp')
@app.post('/api/analyze')
def api_analyze():
    d=request.get_json(silent=True) or {}; ssid=str(d.get('ssid','')).strip(); asset=str(d.get('asset','EURUSD_otc')).strip(); tf=str(d.get('timeframe','1m')).strip(); exp=str(d.get('expiry','1')).strip()
    if not ssid: return jsonify(ok=False,error='SSID is required'),400
    if asset not in ALLOWED_ASSETS: return jsonify(ok=False,error='Invalid asset'),400
    if tf not in TIMEFRAMES: return jsonify(ok=False,error='Invalid timeframe'),400
    if exp not in EXPIRIES: return jsonify(ok=False,error='Invalid expiry'),400
    started=time.time()
    try: r=asyncio.run(fetch(ssid,asset,TIMEFRAMES[tf]))
    except Exception as e: return jsonify(ok=False,stage='quotex_connection_or_candle_fetch',error=type(e).__name__,message=str(e),elapsed_seconds=round(time.time()-started,3)),502
    if not r['ok']: return jsonify(ok=False,asset=asset,timeframe=tf,expiry=exp,elapsed_seconds=round(time.time()-started,3),diagnostics=r,note='Diagnostic only. No order/trade action is performed.'),502
    candles=r['candles']; t=r['transport']; diag={'candle_count':len(candles),'history_request_sent':any(x['event']=='history/load' for x in t['sent']),'history_response_observed':any(x['event'] in ('history/load','history/list/v2','candles') for x in t['received']),'transport':t}
    if not candles: return jsonify(ok=True,signal='NO SIGNAL',asset=asset,timeframe=tf,expiry=exp,elapsed_seconds=round(time.time()-started,3),diagnostics=diag,note='Connected, but no verified candles returned. No fake signal generated.')
    a=analyze_candles(candles); return jsonify(ok=True,asset=asset,timeframe=tf,expiry=exp,elapsed_seconds=round(time.time()-started,3),signal=a['signal'],confidence=a['confidence'],analysis=a,diagnostics=diag,note='Read-only. No order/trade action is performed.')
if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')))
