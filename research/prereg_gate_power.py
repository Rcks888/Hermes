"""Reproduce the drawdown and G2-power table in PREREGISTRATION.md section 3.

Independent trades, win = +1.9 R, loss = -1 R, 20,000 runs per row, seed 2.
G2 is approximated by a normal lower bound at alpha 0.025; the registered rule
is the month-cluster bootstrap of section 5, which this does not implement.
"""
import numpy as np

RUNS = 20000
WIN_R = 1.9
ROWS = ((0.41, 150), (0.43, 100), (0.43, 150), (0.43, 250), (0.45, 150))


def main():
    rng = np.random.default_rng(2)
    print("win  meanR  n    DDp50 DDp95  P>15  P>20  P(G2)")
    for p, n in ROWS:
        r = np.where(rng.random((RUNS, n)) < p, WIN_R, -1.0)
        eq = np.concatenate([np.zeros((RUNS, 1)), np.cumsum(r, 1)], 1)
        dd = (np.maximum.accumulate(eq, 1) - eq).max(1)
        lb = r.mean(1) - 1.96 * r.std(1) / np.sqrt(n)
        q50, q95 = np.quantile(dd, [0.5, 0.95])
        print(f"{p:.2f} {p * WIN_R - (1 - p):.3f} {n:4d} {q50:5.1f} {q95:5.1f}"
              f"  {np.mean(dd > 15):.2f}  {np.mean(dd > 20):.2f}  {np.mean(lb > 0):.2f}")


if __name__ == "__main__":
    main()
