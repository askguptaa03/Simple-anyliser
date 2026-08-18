# Cortex Quotex Signal Generator — read-only MVP

SSID -> Quotex WebSocket -> history/load candles -> EMA/RSI/Bollinger/Stochastic/ADX -> CALL/PUT or NO SIGNAL.

No buy/sell/pending-order method is used. SSID is kept in memory for the request and transport diagnostics record event names only, not payloads.

Uses the unofficial open-source pyquotex client. Quotex can change its private WebSocket protocol at any time. Test with a demo/practice account.
