"""One-off: open a tiny EURUSD test trade on the DEMO account and close it.
Aborts if the connected account is REAL. Scratch script (safe to delete)."""
import sys
import time
import MetaTrader5 as mt5

SYMBOL = "EURUSD"
VOLUME = 0.01
MAGIC = 778899
DEVIATION = 20

def send(req, label):
    r = None
    for fill in (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN):
        req["type_filling"] = fill
        r = mt5.order_send(req)
        if r and r.retcode == mt5.TRADE_RETCODE_DONE:
            return r
        print(f"  {label}: fill={fill} retcode={getattr(r,'retcode',None)} comment={getattr(r,'comment',None)}")
    return r

if not mt5.initialize():
    print("INIT FAILED", mt5.last_error()); sys.exit(1)

acc = mt5.account_info()
if acc is None:
    print("NO ACCOUNT INFO"); mt5.shutdown(); sys.exit(1)

mode = {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(acc.trade_mode, str(acc.trade_mode))
print(f"Account: login={acc.login} server={acc.server} mode={mode} "
      f"equity={acc.equity:.2f} {acc.currency}")
if acc.trade_mode == 2:
    print("ABORT: REAL-money account — refusing to send a test trade."); mt5.shutdown(); sys.exit(2)

info = mt5.symbol_info(SYMBOL)
if info is None:
    print(f"Symbol {SYMBOL} not found"); mt5.shutdown(); sys.exit(1)
if not info.visible:
    mt5.symbol_select(SYMBOL, True)

tick = mt5.symbol_info_tick(SYMBOL)
point = info.point
price = tick.ask
sl = round(price - 200 * point, info.digits)   # ~20 pips
tp = round(price + 200 * point, info.digits)
equity_before = acc.equity

open_req = {
    "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": VOLUME,
    "type": mt5.ORDER_TYPE_BUY, "price": price, "sl": sl, "tp": tp,
    "deviation": DEVIATION, "magic": MAGIC, "comment": "AxonAI TEST",
    "type_time": mt5.ORDER_TIME_GTC,
}
print(f"\nOPEN  BUY {SYMBOL} vol={VOLUME} @ {price:.5f}  SL={sl:.5f}  TP={tp:.5f}")
r = send(open_req, "OPEN")
if not (r and r.retcode == mt5.TRADE_RETCODE_DONE):
    print("OPEN FAILED", getattr(r, "retcode", None), getattr(r, "comment", None))
    mt5.shutdown(); sys.exit(1)
print(f"OPENED order={r.order} deal={r.deal} fill={r.price:.5f} vol={r.volume}")

time.sleep(2)

positions = [p for p in (mt5.positions_get(symbol=SYMBOL) or []) if p.magic == MAGIC]
if not positions:
    print("No open position found to close (SL/TP may have hit)."); mt5.shutdown(); sys.exit(0)
pos = positions[0]
ctick = mt5.symbol_info_tick(SYMBOL)
close_req = {
    "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": pos.volume,
    "type": mt5.ORDER_TYPE_SELL, "position": pos.ticket, "price": ctick.bid,
    "deviation": DEVIATION, "magic": MAGIC, "comment": "AxonAI TEST CLOSE",
    "type_time": mt5.ORDER_TIME_GTC,
}
print(f"\nCLOSE position {pos.ticket} vol={pos.volume} @ {ctick.bid:.5f} (entry {pos.price_open:.5f})")
r2 = send(close_req, "CLOSE")
if not (r2 and r2.retcode == mt5.TRADE_RETCODE_DONE):
    print("CLOSE FAILED", getattr(r2, "retcode", None), getattr(r2, "comment", None))
    mt5.shutdown(); sys.exit(1)
print(f"CLOSED deal={r2.deal} close={r2.price:.5f}")

time.sleep(1)
remaining = [p.ticket for p in (mt5.positions_get(symbol=SYMBOL) or []) if p.magic == MAGIC]
acc2 = mt5.account_info()
print(f"\nRemaining test positions: {remaining if remaining else 'none (fully closed)'}")
print(f"Equity: before={equity_before:.2f} after={acc2.equity:.2f} realized={acc2.equity - equity_before:+.2f} {acc.currency}")
mt5.shutdown()
print("DONE")
