import pandas as pd, numpy as np
def calculate(df):
    x=df.copy()
    x["ema20"]=x.close.ewm(span=20,adjust=False).mean()
    delta=x.close.diff()
    gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean()
    loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    x["rsi14"]=100-100/(1+gain/loss.replace(0,np.nan))
    mid=x.close.rolling(20).mean(); sd=x.close.rolling(20).std()
    x["bb_mid"]=mid; x["bb_upper"]=mid+2*sd; x["bb_lower"]=mid-2*sd
    lo=x.low.rolling(14).min(); hi=x.high.rolling(14).max()
    x["stoch_k"]=100*(x.close-lo)/(hi-lo).replace(0,np.nan)
    x["stoch_d"]=x.stoch_k.rolling(3).mean()
    tr=pd.concat([x.high-x.low,(x.high-x.close.shift()).abs(),(x.low-x.close.shift()).abs()],axis=1).max(axis=1)
    atr=tr.ewm(alpha=1/14,adjust=False).mean()
    up=x.high.diff(); dn=-x.low.diff()
    p=up.where((up>dn)&(up>0),0); m=dn.where((dn>up)&(dn>0),0)
    pdi=100*p.ewm(alpha=1/14,adjust=False).mean()/atr
    mdi=100*m.ewm(alpha=1/14,adjust=False).mean()/atr
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    x["adx14"]=dx.ewm(alpha=1/14,adjust=False).mean()
    return x
