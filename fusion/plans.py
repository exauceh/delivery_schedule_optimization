"""Candidate plans of one couple for one set of allowed weekdays (rules 10-12, 22-25).

A plan maps every line of the couple to a delivery day. Two sources:
- heuristic: greedy filling, then line-by-line local search, then repair of the 'profile reached' rule;
- dynamic programming: best split of the need days into groups of consecutive days, each group delivered
  together on one allowed day, with the 'profile reached' rule inside the recursion.
  Assumption: lines of the same need day travel together and groups are consecutive. The heuristic and the
  final local search work line by line and are not bound by it.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from .data import Calendar
from .loading import Loader


class Planner:
    def __init__(self, cal: Calendar, loader: Loader, anticipation: int, require_profile: bool = True,
                 variant_tolerance: float = 0.0):
        self.cal, self.loader, self.H, self.require_profile = cal, loader, dt.timedelta(days=anticipation), require_profile
        self.variant_tolerance = variant_tolerance   # keep the plans of other reference weeks within this extra cost

    # ----------------------------------------------------------- helpers
    def candidates(self, lines, S):
        days = [d for d in self.cal.open_days if d.weekday() in S]
        cand = {}
        for t, w, v, i in lines:
            c = [d for d in days if t - self.H <= d <= t]
            if not c:
                return None
            cand[i] = c
        return cand

    def profile_reached(self, asg: dict, S) -> bool:
        """Rule 24: one full week delivers on every allowed weekday."""
        if not self.require_profile:
            return True
        used = defaultdict(set)
        for d in set(asg.values()):
            used[Calendar.week_key(d)].add(d.weekday())
        return any(used.get(w, set()) >= set(S) for w in self.cal.full_weeks)

    def evaluate(self, asg: dict, info: dict, row: int):
        by = defaultdict(list)
        for i, d in asg.items():
            by[d].append((info[i][0], info[i][1], i))
        per_day, cost = {}, 0.0
        for d, it in by.items():
            c, n = self.loader.cost(it, row)
            cost += c; per_day[d] = n
        return cost, len(by), sum(per_day.values()), per_day

    # ----------------------------------------------------------- source 1: heuristic
    def _greedy_fill(self, lines, cand):
        left = {x[3]: x for x in sorted(lines, key=lambda x: (x[0], -x[1], x[3]))}
        asg = {}
        while left:
            t, w, v, i = next(iter(left.values()))
            d, cw, cv = cand[i][-1], 0.0, 0.0
            for j, (tj, wj, vj, _) in list(left.items()):
                if d in cand[j] and self.loader.fits(cw + wj, cv + vj):
                    asg[j] = d; cw += wj; cv += vj; del left[j]
                elif tj > d + self.H:
                    break
        return asg

    def _local_search(self, asg, cand, info, row, locked):
        asg, cost = dict(asg), self.loader.cost
        by_date = defaultdict(list)
        for i, d in asg.items():
            by_date[d].append((info[i][0], info[i][1], i))
        dcost = {d: cost(it, row)[0] for d, it in by_date.items()}
        movable = [i for i in sorted(cand) if len(cand[i]) > 1 and i not in locked]
        improved = True
        while improved:
            improved = False
            for i in movable:                                       # move one line
                cur, item = asg[i], (info[i][0], info[i][1], i)
                src = [x for x in by_date[cur] if x[2] != i]
                src_cost = cost(src, row)[0]
                best = (1e-9, None, None)
                for d in cand[i]:
                    if d != cur:
                        dst_cost = cost(by_date.get(d, []) + [item], row)[0]
                        gain = dcost[cur] + dcost.get(d, 0.0) - src_cost - dst_cost
                        if gain > best[0]:
                            best = (gain, d, dst_cost)
                if best[1] is not None:
                    d = best[1]
                    by_date[cur] = src; dcost[cur] = src_cost
                    by_date[d] = by_date.get(d, []) + [item]; dcost[d] = best[2]; asg[i] = d
                    if not src:
                        del by_date[cur]; del dcost[cur]
                    improved = True
            for d0 in sorted(by_date):                              # empty a delivery day
                its = by_date.get(d0)
                if not its or any(len(cand[x[2]]) == 1 or x[2] in locked for x in its):
                    continue
                trial = {k: list(v) for k, v in by_date.items()}
                del trial[d0]
                ok = True
                for x in sorted(its, key=lambda x: (-x[0], x[2])):
                    opts = [d for d in cand[x[2]] if d != d0]
                    if not opts:
                        ok = False; break
                    dbest = min(opts, key=lambda d: (cost(trial.get(d, []) + [x], row)[0] - cost(trial.get(d, []), row)[0], d))
                    trial.setdefault(dbest, []).append(x)
                if ok:
                    tcost = {d: cost(it, row)[0] for d, it in trial.items()}
                    if sum(tcost.values()) < sum(dcost.values()) - 1e-9:
                        by_date, dcost = defaultdict(list, trial), tcost
                        for d, it in trial.items():
                            for x in it:
                                asg[x[2]] = d
                        improved = True
                        break
        return asg

    def _repair(self, asg, cand, info, row, S):
        best = None
        for w in self.cal.full_weeks:
            need_days = [d for d in self.cal.open_days if Calendar.week_key(d) == w and d.weekday() in S]
            trial, locked, ok = dict(asg), set(), True
            for dd in need_days:
                if dd in set(trial.values()):
                    locked |= {i for i, d in trial.items() if d == dd}
                    continue
                opts = [i for i in sorted(cand) if dd in cand[i] and i not in locked]
                if not opts:
                    ok = False; break
                i = min(opts, key=lambda i: (info[i][0], i))
                trial[i] = dd; locked.add(i)
            if not ok:
                continue
            trial = self._local_search(trial, cand, info, row, locked)
            if self.profile_reached(trial, S):
                k = self.evaluate(trial, info, row)[:2]
                if best is None or k < best[0]:
                    best = (k, trial)
        return best[1] if best else None

    def heuristic(self, lines, S, info, row):
        cand = self.candidates(lines, S)
        if cand is None:
            return []
        out = []
        for start in ("fill", "late", "early"):
            asg = self._greedy_fill(lines, cand) if start == "fill" else {i: c[-1 if start == "late" else 0] for i, c in cand.items()}
            asg = self._local_search(asg, cand, info, row, set())
            if not self.profile_reached(asg, S):
                asg = self._repair(asg, cand, info, row, S)
            if asg is not None:
                out.append(asg)
        return out

    # ----------------------------------------------------------- source 2: dynamic programming
    def dynamic(self, lines, S, info, row):
        cap_w, cap_v = self.loader.cfg.truck_kg, self.loader.cfg.truck_m3
        full = [x for x in lines if x[1] >= cap_w - 1e-6 or x[2] >= cap_v - 1e-6]      # a truck on their own
        small = [x for x in lines if not (x[1] >= cap_w - 1e-6 or x[2] >= cap_v - 1e-6)]
        allowed = [d for d in self.cal.open_days if d.weekday() in S]
        by_day = defaultdict(list)
        for t, w, v, i in small:
            by_day[t].append((w, v, i))
        dates = sorted(by_day); n = len(dates)
        cost, window = {}, {}
        for i in range(n):
            for j in range(i, n):
                if dates[j] - dates[i] > self.H:
                    break
                cost[i, j] = self.loader.cost([x for d in dates[i:j + 1] for x in by_day[d]], row)[0]
                window[i, j] = [d for d in allowed if dates[j] - self.H <= d <= dates[i]]
        weeks = self.cal.full_weeks if self.require_profile else [None]
        found = []                                              # best plan for each reference week (rule 24)
        for w in weeks:
            targets = [] if w is None else [d for d in self.cal.open_days if Calendar.week_key(d) == w and d.weekday() in S]
            if w is not None and len(targets) < len(S):
                continue
            fm = (1 << len(targets)) - 1
            f = [[None] * (fm + 1) for _ in range(n + 1)]
            f[n][fm] = ((0.0, 0), [])
            for i in range(n - 1, -1, -1):
                for mask in range(fm + 1):
                    cb = None
                    for j in range(i, n):
                        if (i, j) not in cost:
                            break
                        if not window[i, j]:
                            continue
                        moves = [(None, mask)] + [(k, mask | 1 << k) for k, t in enumerate(targets)
                                                  if not mask >> k & 1 and t in window[i, j]]
                        for k, m2 in moves:
                            nxt = f[j + 1][m2]
                            if nxt is not None:
                                key = (cost[i, j] + nxt[0][0], 1 + nxt[0][1])
                                if cb is None or key < cb[0]:
                                    cb = (key, [(i, j, k)] + nxt[1])
                    f[i][mask] = cb
            if f[0][0] is not None:
                found.append((f[0][0], targets))
        if not found:
            if n or len(S) > 1:
                return []
            found = [(((0.0, 0), []), [])]
        found.sort(key=lambda x: x[0][0])
        best_cost = found[0][0][0][0]
        out = []
        for (_, groups), targets in found:
            if sum(cost[i, j] for i, j, _k in groups) > best_cost * (1 + self.variant_tolerance) + 1e-9:
                continue
            for side in (-1, 0):                                    # latest / earliest day of each window
                asg = {}
                for i, j, k in groups:
                    day = targets[k] if k is not None else window[i, j][side]
                    for d in dates[i:j + 1]:
                        for _, _, lid in by_day[d]:
                            asg[lid] = day
                used, ok = set(asg.values()), True
                for t, w, v, lid in full:                           # on an existing delivery day when possible
                    c = [d for d in used if t - self.H <= d <= t] or [d for d in allowed if t - self.H <= d <= t]
                    if not c:
                        ok = False; break
                    asg[lid] = max(c); used.add(asg[lid])
                if ok and self.profile_reached(asg, S):
                    out.append(asg)
        return out
