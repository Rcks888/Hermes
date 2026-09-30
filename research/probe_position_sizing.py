#!/usr/bin/env python3
"""Priority 0a follow-up — verify position sizing against the terminal.

The feasibility probe returned an internally inconsistent contract spec:

    contract_size = 100    -> 1 lot = 100 oz -> $1 move = $100/lot
    tick_value    = 0.1  per tick_size 0.01 -> $1 move =  $10/lot

A factor of ten apart. risk_manager.calculate_position_size uses
params.get("tick_value", 0.1), and neither tick_value nor tick_size exists in
strategy_params.json, so sizing runs entirely on hardcoded defaults that have
never been checked against the live symbol. If the $100/lot reading is
correct, Hermes sizes ten times too large.

Rather than reason about which field is authoritative, ask the terminal:
order_calc_profit returns the actual P&L for a known move. That is decisive.

Inert while Hermes is alert-only. A capital-loss bug on day one of Phase 3.

Run on the VPS:
    cd /root/Hermes && source venv/bin/activate && export DISPLAY=:99
    python research/probe_position_sizing.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine import mt5_connector
from engine import risk_manager
from engine import version

SYMBOL = "XAUUSD"
OUT_DIR = PROJECT_ROOT / "research" / "data_feasibility"


def main():
    result = {
        "probe_ts": datetime.now(timezone.utc).isoformat(),
        "code_version": version.get_code_version(),
        "symbol": SYMBOL,
    }

    ok, _ = mt5_connector.ensure_connected()
    if not ok:
        print("MT5 not connected — inconclusive, not a negative result.")
        return 1
    mt5 = mt5_connector.mt5

    info = mt5_connector.get_symbol_info(SYMBOL)
    if not info:
        print("Symbol info unavailable.")
        return 1
    for k in ("point", "digits", "tick_size", "tick_value", "contract_size",
              "volume_min", "volume_step"):
        result[k] = info.get(k)

    tick = mt5.symbol_info_tick(SYMBOL)
    price = float(tick.ask) if tick else 4300.0
    result["reference_price"] = price

    # Ground truth: profit on exactly a $1.00 upward move, 1.00 lot.
    one_dollar = None
    try:
        one_dollar = mt5.order_calc_profit(
            mt5.ORDER_TYPE_BUY, SYMBOL, 1.0, price, price + 1.0)
    except Exception as e:
        result["order_calc_profit_error"] = str(e)
    result["actual_profit_1lot_1dollar_move"] = one_dollar

    if one_dollar is None:
        result["error"] = (
            "order_calc_profit unavailable — cannot settle the discrepancy. "
            "Do NOT proceed to Phase 3 sizing until resolved."
        )
        _write(result)
        print(result["error"])
        return 1

    # What each reading of the spec predicts for that same move.
    from_contract = float(info["contract_size"]) * 1.0
    from_tick = (1.0 / float(info["tick_size"])) * float(info["tick_value"])
    result["predicted_by_contract_size"] = from_contract
    result["predicted_by_tick_value"] = from_tick

    def close(a, b):
        return abs(a - b) <= max(0.01, 0.02 * abs(b))

    if close(from_contract, one_dollar):
        result["authoritative_field"] = "contract_size"
    elif close(from_tick, one_dollar):
        result["authoritative_field"] = "tick_value"
    else:
        result["authoritative_field"] = "NEITHER"

    # Correct value-per-point, derived from ground truth.
    true_value_per_point = one_dollar * float(info["point"])
    result["true_value_per_point_per_lot"] = true_value_per_point
    result["hardcoded_default_tick_value"] = 0.1
    result["sizing_error_factor"] = (
        round(true_value_per_point / 0.1, 3) if true_value_per_point else None)

    # What Hermes would actually size today, versus what it should.
    balance = 100_000.0
    acct = mt5_connector.get_account_info()
    if acct and acct.get("balance"):
        balance = float(acct["balance"])
    result["account_balance"] = balance

    sl_distance = 6.53  # roughly one observed ATR
    params = json.load(open(PROJECT_ROOT / "config" / "strategy_params.json"))
    signal = {"sl_distance": sl_distance}
    as_built = risk_manager.calculate_position_size(signal, balance, params)

    risk_amount = balance * params.get("risk_pct", 0.01)
    loss_per_lot = sl_distance * one_dollar
    correct = risk_amount / loss_per_lot if loss_per_lot else 0

    result["test_sl_distance"] = sl_distance
    result["risk_amount"] = risk_amount
    result["lots_as_built"] = as_built
    result["lots_correct"] = round(correct, 2)
    result["realised_risk_as_built"] = round(as_built * loss_per_lot, 2)
    result["realised_risk_pct_as_built"] = round(
        100 * as_built * loss_per_lot / balance, 2)
    result["notional_as_built"] = round(
        as_built * float(info["contract_size"]) * price, 2)
    result["leverage_as_built"] = round(
        as_built * float(info["contract_size"]) * price / balance, 1)

    if as_built > 0 and correct > 0:
        result["oversize_factor"] = round(as_built / correct, 2)

    f = result.get("oversize_factor") or 1
    if f > 1.1:
        result["verdict"] = (
            f"SIZING DEFECT — Hermes sizes {f}x too large, realising "
            f"{result['realised_risk_pct_as_built']}% risk instead of "
            f"{100 * params.get('risk_pct', 0.01):.1f}%. Read tick_value from "
            "the live symbol spec, config as override only. Blocks Phase 3."
        )
    elif f < 0.9:
        result["verdict"] = f"Hermes sizes {1/f:.2f}x too small. Under-risking."
    else:
        result["verdict"] = (
            "Sizing agrees with the terminal. Still move tick_value and "
            "tick_size out of hardcoded defaults into the live symbol spec."
        )

    _write(result)
    _summarise(result, params)
    return 0


def _write(result):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT_DIR / f"position_sizing_{stamp}.json"
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nWritten: {path}")


def _summarise(r, params):
    print("\n" + "=" * 66)
    print("POSITION SIZING VERIFICATION")
    print("=" * 66)
    print(f"  reference price   {r.get('reference_price')}")
    print(f"  contract_size     {r.get('contract_size')}")
    print(f"  tick_size         {r.get('tick_size')}   tick_value {r.get('tick_value')}")
    print(f"\n  GROUND TRUTH (order_calc_profit, 1.00 lot, $1.00 move)")
    print(f"    actual                  ${r.get('actual_profit_1lot_1dollar_move')}")
    print(f"    predicted by contract   ${r.get('predicted_by_contract_size')}")
    print(f"    predicted by tick_value ${r.get('predicted_by_tick_value')}")
    print(f"    authoritative field     {r.get('authoritative_field')}")
    print(f"\n  value per point   ${r.get('true_value_per_point_per_lot')} "
          f"(hardcoded default ${r.get('hardcoded_default_tick_value')}, "
          f"factor {r.get('sizing_error_factor')}x)")
    print(f"\n  SIZING AT SL {r.get('test_sl_distance')} ON BALANCE {r.get('account_balance')}")
    print(f"    intended risk     {100 * params.get('risk_pct', 0.01):.1f}%  "
          f"(${r.get('risk_amount')})")
    print(f"    lots as built     {r.get('lots_as_built')}")
    print(f"    lots correct      {r.get('lots_correct')}")
    print(f"    realised risk     ${r.get('realised_risk_as_built')}  "
          f"({r.get('realised_risk_pct_as_built')}%)")
    print(f"    notional          ${r.get('notional_as_built'):,}  "
          f"leverage {r.get('leverage_as_built')}:1")
    print(f"    oversize factor   {r.get('oversize_factor')}x")
    print(f"\n  VERDICT")
    print(f"  {r.get('verdict')}")
    print("=" * 66)


if __name__ == "__main__":
    sys.exit(main())