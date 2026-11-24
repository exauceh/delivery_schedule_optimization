"""Truck loading (rules 13-16, 21): fast packing for planning, exact packing for the published plan."""
from __future__ import annotations

from ortools.sat.python import cp_model

from .config import Config
from .data import RateSheet


class Loader:
    def __init__(self, cfg: Config, rates: RateSheet):
        self.cfg, self.rates, self._cache = cfg, rates, {}

    def fits(self, w: float, v: float) -> bool:
        return w <= self.cfg.truck_kg + 1e-9 and v <= self.cfg.truck_m3 + 1e-9

    def ffd(self, items: list[tuple[float, float, int]], row: int) -> list[list]:
        """First-Fit Decreasing on weight, checking weight and volume. items: (weight, volume, line_id)."""
        bins: list[list] = []
        for w, v, i in sorted(items, key=lambda x: (-x[0], -x[1], x[2])):
            for b in bins:
                if self.fits(b[0] + w, b[1] + v):
                    b[0] += w; b[1] += v; b[2].append(i); break
            else:
                bins.append([w, v, [i]])
        return bins

    def cost(self, items: list[tuple[float, float, int]], row: int) -> tuple[float, int]:
        """(cost, trucks) of lines delivered together, with the fast packing (memoised)."""
        key = (row, frozenset(x[2] for x in items))
        if key not in self._cache:
            bins = self.ffd(items, row) if items else []
            self._cache[key] = (sum(self.rates.price(row, b[0]) for b in bins), len(bins))
        return self._cache[key]

    def exact(self, items: list[tuple[float, float, int]], row: int) -> list[list]:
        """Exact packing of one delivery: fewest trucks, then lowest cost (each truck priced on its own weight).
        Used only when the fast packing needs more than one truck."""
        fast = self.ffd(items, row)
        if len(fast) <= 1:
            return fast
        n, B = len(items), len(fast)
        price = [round(p * 100) for p in self.rates.grid[row] * self.rates.factor]
        ub = [int(round(min(b, self.cfg.truck_kg) * 100)) for b in self.rates.kg_bounds]
        W = [int(round(x[0] * 100)) for x in items]; V = [int(round(x[1] * 1000)) for x in items]
        m = cp_model.CpModel()
        a = {(i, b): m.NewBoolVar("") for i in range(n) for b in range(B)}
        t = [m.NewBoolVar("") for _ in range(B)]
        y = {(b, k): m.NewBoolVar("") for b in range(B) for k in range(len(price))}
        for i in range(n):
            m.AddExactlyOne(a[i, b] for b in range(B))
        for b in range(B):
            m.Add(sum(y[b, k] for k in range(len(price))) == t[b])
            m.Add(sum(W[i] * a[i, b] for i in range(n)) <= sum(ub[k] * y[b, k] for k in range(len(price))))
            m.Add(sum(V[i] * a[i, b] for i in range(n)) <= int(round(self.cfg.truck_m3 * 1000)) * t[b])
            if b:
                m.Add(t[b] <= t[b - 1])
        for b, bn in enumerate(fast):                                       # start from the fast packing
            for i in range(n):
                m.AddHint(a[i, b], items[i][2] in bn[2])
        m.Minimize(sum(t) * 10**9 + sum(price[k] * y[b, k] for b in range(B) for k in range(len(price))))
        s = cp_model.CpSolver()
        s.parameters.num_workers = 1
        s.parameters.max_deterministic_time = 2.0
        s.parameters.random_seed = self.cfg.solver_seed
        if s.Solve(m) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return fast
        bins = []
        for b in range(B):
            ids = [i for i in range(n) if s.Value(a[i, b])]
            if ids:
                bins.append([sum(items[i][0] for i in ids), sum(items[i][1] for i in ids), [items[i][2] for i in ids]])
        exact_cost = sum(self.rates.price(row, b[0]) for b in bins)
        fast_cost = sum(self.rates.price(row, b[0]) for b in fast)
        return bins if (len(bins), exact_cost) <= (len(fast), fast_cost) else fast
