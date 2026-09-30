#!/usr/bin/env python3
"""Hermes 2-week review: why no signals, plus the two alert events."""
import json, collections, os

EV = "logs/events.jsonl"
SOAK = "logs/soak.json"

print("=" * 72)
print("1. BLOCKER HISTOGRAM — ALL SESSIONS")
print("=" * 72)
rows = []
with open(EV) as f:
    for l in f:
        try: rows.append(json.loads(l))
        except Exception: pass
print(f"total events: {len(rows)}")
c = collections.Counter(e.get("blocked_at", "-").split(" (")[0] for e in rows)
for k, v in c.most_common():
    print(f"{v:6d}  {k}")

print()
print("=" * 72)
print("2. BLOCKER HISTOGRAM — LONDON + NY ONLY (the ones that can trade)")
print("=" * 72)
tradeable = [e for e in rows if e.get("session") in ("LONDON", "NY")]
print(f"tradeable cycles: {len(tradeable)}")
c2 = collections.Counter(e.get("blocked_at", "-").split(" (")[0] for e in tradeable)
for k, v in c2.most_common():
    pct = v / len(tradeable) * 100 if tradeable else 0
    print(f"{v:6d}  {pct:5.1f}%  {k}")

print()
print("=" * 72)
print("3. HOW FAR DID ANY CYCLE GET? (deepest gate reached)")
print("=" * 72)
ORDER = ["trend_not_confirmed","no_pullback","no_candle_confirmation",
         "vwap_misaligned","volume_too_low","atr_too_low","atr_invalid",
         "sl_too_tight","sl_too_wide","sl_distance_invalid","OK"]
seen = {k: 0 for k in ORDER}
for e in tradeable:
    b = e.get("blocked_at", "-").split(" (")[0]
    if b in seen: seen[b] += 1
for i, k in enumerate(ORDER):
    marker = "  <-- deepest reached" if seen[k] and all(seen[j] == 0 for j in ORDER[i+1:]) else ""
    print(f"  gate {i+1:2d}. {k:26s} {seen[k]:5d}{marker}")

print()
print("=" * 72)
print("4. TREND CONFIRMATION RATE")
print("=" * 72)
conf = sum(1 for e in tradeable if e.get("trend_confirmed"))
print(f"trend_confirmed=True in {conf}/{len(tradeable)} tradeable cycles"
      f" ({conf/len(tradeable)*100:.1f}%)" if tradeable else "no data")
tr = collections.Counter(e.get("trend") for e in tradeable)
print("trend distribution:", dict(tr))

print()
print("=" * 72)
print("5. SWAP / PSI AROUND THE THRASHING EVENT")
print("=" * 72)
if os.path.exists(SOAK):
    soak = json.load(open(SOAK))
    cy = soak.get("cycles", [])
    print(f"soak cycles recorded: {len(cy)}")
    rec = []
    for i in range(1, len(cy)):
        a, b = cy[i-1].get("resources", {}), cy[i].get("resources", {})
        if "pswpout" in a and "pswpout" in b:
            d = b["pswpout"] - a["pswpout"]
            psi = None
            if "psi_mem_full_total" in a and "psi_mem_full_total" in b:
                psi = b["psi_mem_full_total"] - a["psi_mem_full_total"]
            rec.append((cy[i].get("timestamp"), d, b.get("swap_used_mb"),
                        b.get("ram_free_mb"), psi))
    rec.sort(key=lambda r: -r[1])
    print()
    print("top 10 swap-out spikes (pages out, swap MB, RAM free MB, PSI full us):")
    for ts, d, sw, rf, psi in rec[:10]:
        mb = d * 4 / 1024
        print(f"  {ts}  {d:8d} pages ({mb:7.1f} MB)  swap={sw}MB free={rf}MB  psi_full={psi}")
    nz = [r for r in rec if r[4] and r[4] > 0]
    print()
    print(f"cycles with ANY PSI full stall: {len(nz)}/{len(rec)}")
    for ts, d, sw, rf, psi in sorted(nz, key=lambda r: -(r[4] or 0))[:5]:
        print(f"  {ts}  psi_full={psi}us ({psi/1e6:.3f}s)  {d} pages out  free={rf}MB")
else:
    print("soak.json not found")

print()
print("=" * 72)
print("6. MT5 FAILURES")
print("=" * 72)
ab = [e for e in rows if e.get("event") == "CYCLE_ABORTED"]
print(f"aborted cycles in events.jsonl: {len(ab)}")
for e in ab:
    print(f"  {e.get('ts')}  {e.get('reason')}  failures={e.get('consecutive_failures')}")
if os.path.exists(SOAK):
    bad = [c for c in json.load(open(SOAK)).get("cycles", []) if not c.get("connected")]
    print(f"failed soak cycles: {len(bad)}")
    for c in bad[-10:]:
        print(f"  {c.get('timestamp')}  err={c.get('error')}"
              f"  init_latency={c.get('init_latency_s')}s"
              f"  rpyc_restarted={c.get('rpyc_restarted')}")
