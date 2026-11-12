"""Entry, sizing, margin and R accounting (contract C3.3-C3.4, C4.1-C4.5,
C5.7, section 7 R1).

Sizing and validity come from engine.entry_observation.propose_execution, the
function live logs from (C4.1). Nothing here reimplements it. The only
addition is the margin comparison of C4.4, which live never makes.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import entry_observation as eo  # noqa: E402

BROKER_SPEC = ROOT / "research" / "data_feasibility" / "broker_spec_20261105T100940Z.json"
MARGIN_INSUFFICIENT = "margin_insufficient"


class RunInvalid(Exception):
    """margin_not_checked in replay (C4.4): the run is invalid."""


class MarginCalculator:
    """order_calc_margin stand-in for propose_execution's `mt5` argument (C4.4)."""
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self, margin_rate, contract_size):
        self.margin_rate = float(margin_rate)
        self.contract_size = float(contract_size)

    def order_calc_margin(self, order_type, symbol, volume, price):
        return volume * self.contract_size * price * self.margin_rate


class BrokerSpec:
    def __init__(self, raw):
        self.raw = raw
        sym = raw["spec"]["symbol"]
        self.version = raw["broker_spec_version"]
        self.point = sym["point"]
        self.digits = sym["digits"]
        self.symbol_info = {"contract_size": sym["trade_contract_size"],
                            "volume_min": sym["volume_min"], "volume_step": sym["volume_step"],
                            "volume_max": sym["volume_max"]}
        self.margin_rate = raw["spec"]["margin_probe"]["margin_rate"]

    @classmethod
    def load(cls, path=BROKER_SPEC):
        return cls(json.loads(Path(path).read_text()))

    def margin_calculator(self):
        return MarginCalculator(self.margin_rate, self.symbol_info["contract_size"])


def build_quote(bid, ask):
    return {"quote_bid": bid, "quote_ask": ask, "quote_error": None}


def propose(signal, quote, balance, params, symbol_info, mt5):
    return eo.propose_execution(signal, quote, balance, params, symbol_info, mt5=mt5)


def execute(signal, quote, balance, params, spec, open_margin=0.0):
    """propose_execution with the C4.4 calculator, then the margin check."""
    out = propose(signal, quote, balance, params, spec.symbol_info, spec.margin_calculator())
    status = out["execution_validity_status"]
    if status == "margin_not_checked":
        raise RunInvalid(out["execution_validity_detail"])
    if status == eo.EXEC_OK:
        free = balance - open_margin
        if out["required_margin"] > free:
            out["execution_validity_status"] = MARGIN_INSUFFICIENT
            out["execution_validity_detail"] = (
                f"required_margin {out['required_margin']} > free {round(free, 2)}")
    return out


def population(out):
    """'C' for an executable opportunity, else 'B' only (C4.5)."""
    return "C" if out["execution_validity_status"] == eo.EXEC_OK else "B"


def levels(direction, bid, sl, tp, spread_price):
    """Fill and bid-space trigger levels (C3.4), from entry_observation."""
    return eo.spread_accounting(direction, bid, sl, tp, spread_price)


def realised_r(direction, fill, sl, exit_leg):
    """C5.7. exit_leg is the closing-leg price: bid for LONG, bid + spread for SHORT."""
    if direction == "LONG":
        return (exit_leg - fill) / (fill - sl)
    return (fill - exit_leg) / (sl - fill)


def r1_q(out):
    """Section 7 R1: executable / original stop distance. Report-only."""
    d0 = abs(float(out["signal_close"]) - float(out["original_sl"]))
    d = out.get("executable_sl_distance")
    return None if d is None or d0 <= 0 else d / d0
