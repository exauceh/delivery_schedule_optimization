"""Smoothing per plant (rules 27-29) with OR-Tools CP-SAT, deterministic (fixed seed, deterministic time, 1 worker)."""
from __future__ import annotations

from ortools.sat.python import cp_model

from .config import Config


def _solver(cfg: Config) -> cp_model.CpSolver:
    s = cp_model.CpSolver()
    s.parameters.num_workers = 1
    s.parameters.random_seed = cfg.solver_seed
    s.parameters.max_deterministic_time = cfg.solver_deterministic_time
    return s


def smooth_plant(cfg: Config, keys: list, alts: dict) -> tuple[dict, dict]:
    """Pick one plan per couple of the plant: lowest daily peak within the allowed extra cost (0 = minimal cost), then lowest cost, then fewest trucks.
    alts[k][0] is the cheapest plan of couple k (reference before smoothing)."""
    base_cents = sum(round(alts[k][0]["cost"] * 100) for k in keys)   # same rounding as the model: the reference is always feasible
    base_trucks = sum(alts[k][0]["trucks"] for k in keys)
    days = sorted({d for k in keys for a in alts[k] for d in a["per_day"]})

    def solve(peak=None):
        m = cp_model.CpModel()
        b = {(k, i): m.NewBoolVar("") for k in keys for i in range(len(alts[k]))}
        for k in keys:
            m.AddExactlyOne(b[k, i] for i in range(len(alts[k])))
            m.AddHint(b[k, 0], True)
        P = m.NewIntVar(0, 10_000, "peak")
        for d in days:
            m.Add(sum(a["per_day"].get(d, 0) * b[k, i] for k in keys for i, a in enumerate(alts[k])) <= P)
        cost = sum(round(a["cost"] * 100) * b[k, i] for k in keys for i, a in enumerate(alts[k]))
        trucks = sum(a["trucks"] * b[k, i] for k in keys for i, a in enumerate(alts[k]))
        m.Add(cost <= int(base_cents * (1 + cfg.smoothing_max_extra_cost)))      # rule 28
        m.Add(trucks <= base_trucks)                                                  # rule 29, plant by plant
        if peak is None:
            m.Minimize(P)                                                                 # rule 27
        else:
            m.Add(P <= peak)
            m.Minimize(cost * 1000 + trucks)                                              # lowest cost, then fewest trucks
        s = _solver(cfg)
        st = s.Solve(m)
        return s, s.StatusName(st), b, P

    s, st1, b, P = solve()
    if st1 not in ("OPTIMAL", "FEASIBLE"):
        raise RuntimeError(f"smoothing: no solution ({st1})")
    peak = s.Value(P)
    s, st2, b, P = solve(peak)
    if st2 not in ("OPTIMAL", "FEASIBLE"):
        raise RuntimeError(f"smoothing: no solution at peak {peak} ({st2})")
    peak = s.Value(P)
    choice = {k: next(i for i in range(len(alts[k])) if s.Value(b[k, i])) for k in keys}
    return choice, {"pic": peak, "statut_pic": st1, "statut_cout": st2}
