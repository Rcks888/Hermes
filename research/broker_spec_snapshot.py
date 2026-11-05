#!/usr/bin/env python3
"""Versioned broker-spec snapshot for the replay (contract C4.2, C4.4, C3.7, TBD-5).

Reads XAUUSD symbol properties, account leverage and one margin calculation
from the live terminal, and writes them with a SHA-256 of their canonical JSON.
`broker_spec_version` = "bs-" + the first 12 hex digits. Swap fields feed the
C3.7 current-rate estimate only; they are today's rates, not a history.
Holds the MT5 run lock, so run it just after a :08/:23/:38/:53 cycle:

    cd /root/Hermes && source venv/bin/activate && export DISPLAY=:99
    python research/broker_spec_snapshot.py
"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
OUT_DIR = PROJECT_ROOT / "research" / "data_feasibility"

SYMBOL_FIELDS = (
    "name", "point", "digits", "trade_contract_size", "trade_tick_size", "trade_tick_value",
    "volume_min", "volume_max", "volume_step", "trade_calc_mode", "trade_mode",
    "trade_stops_level", "trade_freeze_level", "filling_mode", "currency_base",
    "currency_profit", "currency_margin", "margin_initial", "margin_maintenance",
    "swap_mode", "swap_long", "swap_short", "swap_rollover3days",
)
ACCOUNT_FIELDS = ("server", "currency", "leverage", "margin_mode", "trade_mode")
WEEKDAY_MT5 = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def canonical_hash(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def margin_rate(margin, volume, contract_size, price):
    return margin / (volume * contract_size * price)


def selftest():
    ok = True

    def check(name, cond):
        nonlocal ok
        ok &= bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")

    a = {"symbol": {"swap_long": -1.0, "point": 0.01}, "account": {"leverage": 100}}
    b = {"account": {"leverage": 100}, "symbol": {"point": 0.01, "swap_long": -1.0}}
    check("hash ignores key order", canonical_hash(a) == canonical_hash(b))
    check("hash changes with a value", canonical_hash(a) != canonical_hash({**a, "account": {"leverage": 200}}))
    check("margin rate 1% reproduces C4.4 (1,642.26 for 0.40 lots at 4105.66)",
          abs(round(0.40 * 100 * 4105.66 * margin_rate(1642.264, 0.40, 100, 4105.66), 2) - 1642.26) < 1e-9
          and abs(margin_rate(1642.264, 0.40, 100, 4105.66) - 0.01) < 1e-6)
    return ok


def main():
    print("Self-test:")
    if not selftest():
        print("SELF-TEST FAILED")
        return 1
    if "--self-test" in sys.argv:
        print("ALL PASS")
        return 0
    from engine import mt5_connector, version
    if not mt5_connector.acquire_run_lock(wait_seconds=60):
        print("MT5 lock held by a cron run; start again after it finishes")
        return 1
    ok, _ = mt5_connector.ensure_connected()
    if not ok:
        print("MT5 not connected")
        return 1
    m = mt5_connector.mt5
    info, acc, tick = m.symbol_info("XAUUSD"), m.account_info(), m.symbol_info_tick("XAUUSD")
    sym = {f: getattr(info, f, None) for f in SYMBOL_FIELDS}
    account = {f: getattr(acc, f, None) for f in ACCOUNT_FIELDS}
    margin = m.order_calc_margin(m.ORDER_TYPE_BUY, "XAUUSD", 1.0, tick.ask)
    spec = {
        "symbol": sym, "account": account,
        "margin_probe": {"order": "BUY", "volume": 1.0, "price": tick.ask, "margin": margin,
                         "margin_rate": round(margin_rate(margin, 1.0, sym["trade_contract_size"], tick.ask), 6)},
        "swap_triple_weekday": WEEKDAY_MT5[sym["swap_rollover3days"]] if sym["swap_rollover3days"] is not None else None,
    }
    h = canonical_hash(spec)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rec = {"ts": ts, "code_version": version.get_code_version(), "broker_spec_version": "bs-" + h[:12],
           "sha256": h, "spec": spec}
    out = OUT_DIR / f"broker_spec_{ts}.json"
    out.write_text(json.dumps(rec, indent=1, default=str))
    print(json.dumps(rec, indent=1, default=str))
    print(f"Written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
