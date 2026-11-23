#!/usr/bin/env python3
"""
Consolidation du planning de livraison - moteur fusionné (profil A + lissage).

Usage : python consolidation_fusion.py <released_schedule.csv> <distances.csv> [dossier_sortie]

Étapes :
  1. Préparation des données (règles 1 à 6).
  2. Plans par couple et par jeu de jours autorisés, de deux sources :
       a. heuristique (remplissage, puis recherche locale, puis réparation du « profil atteint ») ;
       b. programmation dynamique exacte sur les jours de besoin (avec « profil atteint » dans le calcul).
  3. Lissage par usine (OR-Tools CP-SAT) : pic minimal (surcoût <= 1 %), puis moins de camions, puis coût.
  4. Amélioration locale ligne par ligne (sans dépasser le pic ni casser le profil).
  5. Contrôles et sorties (sortie 1 profils, sortie 2 camions, sortie 3 saving, classeur Excel).
"""
import datetime as dt
import itertools
import os
import sys
import time
from collections import defaultdict

import pandas as pd
from ortools.sat.python import cp_model

T0 = time.time()

# ------------------------------------------------------------------ paramètres
H_START, H_END = dt.date(2026, 11, 9), dt.date(2026, 12, 18)    # règle 1
CAP_KG, CAP_M3 = 24500.0, 110.4                                   # règle 13
ANTICIP = 7                                                       # règle 12
MARGE, DIESEL = 2.3923, 1.1943                                    # règle 19
LISSAGE_MAX = 0.01                                                # règle 28
REMOVE_WEIGHT = 26905.2                                           # règle 4
SMOOTH_TIME = 5.0                                                 # s, CP-SAT par usine et par étape
JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]

KM = [30, 50, 75, 100, 125, 150, 175, 200, 250, 300, 400, 500, 650, float("inf")]
KG = [50, 100, 200, 300, 400, 500, 750, 1000, 1500, 2000, 2500, 3000, 3500, 4000, float("inf")]
GRID = [
 [4.75, 6.66, 8.56, 10.73, 13.06, 14.75, 17.75, 22.35, 25.67, 31.40, 35.08, 35.16, 37.73, 40.87, 42.42],
 [5.78, 7.94, 10.74, 13.07, 15.45, 17.35, 20.96, 25.32, 30.05, 35.97, 40.52, 42.90, 47.04, 51.05, 52.80],
 [6.92, 9.54, 13.14, 15.99, 18.43, 20.59, 24.96, 29.02, 35.52, 41.70, 47.33, 52.58, 57.55, 62.30, 64.28],
 [7.33, 10.27, 14.35, 17.61, 20.40, 22.88, 27.96, 32.63, 40.20, 47.30, 53.77, 59.79, 65.47, 70.89, 73.04],
 [7.77, 10.99, 15.57, 19.24, 22.37, 25.17, 30.95, 36.25, 44.89, 52.91, 60.21, 66.99, 73.38, 79.47, 81.80],
 [8.26, 11.83, 17.04, 21.23, 24.84, 28.09, 34.86, 41.01, 51.20, 60.59, 69.12, 77.06, 84.54, 91.65, 94.22],
 [9.85, 14.04, 19.96, 24.65, 28.69, 32.28, 39.74, 46.45, 57.51, 68.25, 78.04, 87.13, 95.69, 103.83, 106.65],
 [11.45, 16.31, 23.21, 28.67, 33.33, 37.49, 46.16, 53.90, 66.69, 78.23, 88.63, 98.88, 108.54, 117.72, 120.80],
 [13.56, 19.66, 28.43, 35.44, 41.45, 46.84, 58.22, 68.33, 85.24, 100.38, 114.05, 126.66, 138.46, 149.64, 153.36],
 [15.68, 23.00, 33.66, 42.21, 49.58, 56.20, 70.29, 82.77, 103.80, 122.55, 139.46, 155.07, 169.70, 183.52, 187.92],
 [17.28, 25.28, 36.93, 46.44, 54.85, 62.43, 78.72, 93.18, 117.74, 139.65, 159.50, 177.84, 195.04, 211.32, 216.29],
 [18.89, 27.59, 40.93, 51.67, 60.99, 69.37, 87.41, 103.36, 130.48, 154.59, 176.39, 196.53, 215.39, 233.24, 238.63],
 [21.49, 31.88, 47.21, 59.52, 70.20, 79.79, 100.44, 118.63, 149.59, 177.00, 201.74, 224.55, 245.91, 266.09, 272.15],
 [26.04, 38.57, 57.05, 71.86, 84.66, 96.17, 120.96, 142.72, 179.76, 212.39, 241.82, 268.92, 294.24, 318.15, 325.25],
]
FACT = (1 + MARGE) * DIESEL


def row_of(km):
    return next(i for i, b in enumerate(KM) if km <= b)


def truck_price(row, kg):
    col = next(i for i, b in enumerate(KG) if kg <= b + 1e-9)
    return GRID[row][col] * FACT


# ------------------------------------------------------------------ calendrier
def german_holidays():
    try:
        import holidays
        return set(holidays.Germany(years=[2026, 2027]).keys())
    except ImportError:
        return {dt.date(2026, 10, 3), dt.date(2026, 12, 25), dt.date(2026, 12, 26), dt.date(2027, 1, 1)}


HOL = german_holidays()
D_START = H_START - dt.timedelta(days=ANTICIP)                     # règle 9 : 02/11
OPEN_DAYS = [D_START + dt.timedelta(days=i) for i in range((H_END - D_START).days + 1)]
OPEN_DAYS = [d for d in OPEN_DAYS if d.weekday() < 5 and d not in HOL]   # règles 7-8


def week_key(d):
    y, w, _ = d.isocalendar()
    return (y, w)


FULL_WEEKS = sorted({week_key(d) for d in OPEN_DAYS
                     if d - dt.timedelta(days=d.weekday()) >= D_START
                     and d - dt.timedelta(days=d.weekday()) + dt.timedelta(days=4) <= H_END})
SUBSETS = [c for k in range(1, 6) for c in itertools.combinations(range(5), k)]   # règles 22-23


# ------------------------------------------------------------------ données
def load(schedule_path, dist_path):
    raw = pd.read_csv(schedule_path, encoding="utf-8-sig")
    log = {"lignes_brutes": len(raw)}
    raw["date"] = pd.to_datetime(raw["date"]).dt.date
    d = raw[raw["date"] != dt.date(2027, 1, 1)]                          # règle 2
    log["exclues_cumul_0101"] = len(raw) - len(d)
    n = len(d); d = d[(d["date"] >= H_START) & (d["date"] <= H_END)]    # règle 1
    log["exclues_hors_horizon"] = n - len(d)
    n = len(d); d = d[d["schedule_quantity"] > 0]                        # règle 3
    log["exclues_quantite_0"] = n - len(d)
    n = len(d); d = d[~((d["weight"] - REMOVE_WEIGHT).abs() < 0.05)]     # règle 4
    log["exclues_ligne_26_9t"] = n - len(d)
    log["poids_manquant_remplace"] = int(d["weight"].isna().sum())
    log["volume_manquant_remplace"] = int(d["volume"].isna().sum())
    d = d.assign(weight_missing=d["weight"].isna() | d["volume"].isna())
    d["weight"] = d["weight"].fillna(CAP_KG)                             # règle 5
    d["volume"] = d["volume"].fillna(CAP_M3)
    dist = pd.read_csv(dist_path)
    dmax = dist["distance"].max()
    miss = sorted(set(d["supplier_id"]) - set(dist.dropna()["supplier_id"]))
    log["distance_manquante_remplacee"] = ", ".join(miss) + f" -> {dmax:g} km"
    dist = dist.set_index("supplier_id")["distance"].fillna(dmax)        # règle 6
    d["distance"] = d["supplier_id"].map(dist).fillna(dmax)
    if ((d["weight"] > CAP_KG) | (d["volume"] > CAP_M3)).any():
        raise ValueError("une ligne dépasse la capacité d'un camion")
    d = d.reset_index(drop=True)
    d["line_id"] = d.index
    log["lignes_retenues"] = len(d)
    return d, log


# ------------------------------------------------------------------ chargement des camions
def pack(items, row):
    """items: (w, v, id). First-Fit Decreasing, contrôle poids et volume (règles 13, 16)."""
    if not items:
        return 0.0, []
    bins = []
    for w, v, i in sorted(items, key=lambda x: (-x[0], -x[1])):
        for b in bins:
            if b[0] + w <= CAP_KG + 1e-9 and b[1] + v <= CAP_M3 + 1e-9:
                b[0] += w; b[1] += v; b[2].append(i); break
        else:
            bins.append([w, v, [i]])
    return sum(truck_price(row, b[0]) for b in bins), bins


_PC = {}


def pack_cost(items, row):
    k = (row, frozenset(x[2] for x in items))
    if k not in _PC:
        c, b = pack(items, row)
        _PC[k] = (c, len(b))
    return _PC[k]


def evaluate(asg, info, row):
    """asg {line_id: date} -> (cost, deliveries, trucks, trucks_per_day)."""
    by = defaultdict(list)
    for i, d in asg.items():
        by[d].append((info[i][0], info[i][1], i))
    cost, per_day = 0.0, {}
    for d, it in by.items():
        c, n = pack_cost(it, row)
        cost += c; per_day[d] = n
    return cost, len(by), sum(per_day.values()), per_day


def profile_reached(asg, S):
    """règle 24 : une semaine complète livre tous les jours autorisés."""
    used = defaultdict(set)
    for d in set(asg.values()):
        used[week_key(d)].add(d.weekday())
    return any(used.get(w, set()) >= set(S) for w in FULL_WEEKS)


def candidates(lines, S):
    ddays = [d for d in OPEN_DAYS if d.weekday() in S]
    cand = {}
    for t, w, v, i in lines:
        c = [d for d in ddays if t - dt.timedelta(days=ANTICIP) <= d <= t]   # règles 11-12
        if not c:
            return None
        cand[i] = c
    return cand


# ------------------------------------------------------------------ source a : heuristique
def greedy_fill(lines, cand):
    order = sorted(lines, key=lambda x: (x[0], -x[1]))
    left = {x[3]: x for x in order}
    asg = {}
    while left:
        t, w, v, i = next(iter(left.values()))
        d = cand[i][-1]
        cw = cv = 0.0
        for j, (tj, wj, vj, _) in list(left.items()):
            if d in cand[j] and cw + wj <= CAP_KG + 1e-9 and cv + vj <= CAP_M3 + 1e-9:
                asg[j] = d; cw += wj; cv += vj
                del left[j]
            elif tj > d + dt.timedelta(days=ANTICIP):
                break
    return asg


def local_search(asg, cand, info, row, locked):
    asg = dict(asg)
    by_date = defaultdict(list)
    for i, d in asg.items():
        by_date[d].append((info[i][0], info[i][1], i))
    dcost = {d: pack_cost(it, row)[0] for d, it in by_date.items()}
    movable = [i for i, c in cand.items() if len(c) > 1 and i not in locked]
    improved = True
    while improved:
        improved = False
        for i in movable:                                     # déplacer une ligne
            cur = asg[i]
            item = (info[i][0], info[i][1], i)
            src = [x for x in by_date[cur] if x[2] != i]
            src_cost = pack_cost(src, row)[0]
            best_gain, best_d, best_dc = 1e-9, None, None
            for d in cand[i]:
                if d == cur:
                    continue
                dst_cost = pack_cost(by_date.get(d, []) + [item], row)[0]
                gain = dcost[cur] + dcost.get(d, 0.0) - src_cost - dst_cost
                if gain > best_gain:
                    best_gain, best_d, best_dc = gain, d, dst_cost
            if best_d is not None:
                by_date[cur] = src; dcost[cur] = src_cost
                by_date[best_d] = by_date.get(best_d, []) + [item]; dcost[best_d] = best_dc
                asg[i] = best_d
                if not src:
                    del by_date[cur]; del dcost[cur]
                improved = True
        for d0 in sorted(by_date):                            # vider un jour de livraison
            its = by_date.get(d0)
            if not its or any(len(cand[x[2]]) == 1 or x[2] in locked for x in its):
                continue
            trial = {k: list(v) for k, v in by_date.items()}
            del trial[d0]
            ok = True
            for x in sorted(its, key=lambda x: -x[0]):
                opts = [d for d in cand[x[2]] if d != d0]
                if not opts:
                    ok = False; break
                dbest = min(opts, key=lambda d: pack_cost(trial.get(d, []) + [x], row)[0] - pack_cost(trial.get(d, []), row)[0])
                trial.setdefault(dbest, []).append(x)
            if not ok:
                continue
            tcost = {d: pack_cost(it, row)[0] for d, it in trial.items()}
            if sum(tcost.values()) < sum(dcost.values()) - 1e-9:
                by_date, dcost = defaultdict(list, trial), tcost
                for d, it in trial.items():
                    for x in it:
                        asg[x[2]] = d
                improved = True
                break
    return asg


def repair_profile(asg, cand, info, row, S):
    best = None
    for w in FULL_WEEKS:
        need_days = [d for d in OPEN_DAYS if week_key(d) == w and d.weekday() in S]
        trial = dict(asg); locked = set(); ok = True
        used = set(trial.values())
        for dd in need_days:
            if dd in used:
                locked |= {i for i, d in trial.items() if d == dd}
                continue
            opts = [i for i, c in cand.items() if dd in c and i not in locked]
            if not opts:
                ok = False; break
            i = min(opts, key=lambda i: info[i][0])
            trial[i] = dd; locked.add(i); used = set(trial.values())
        if not ok:
            continue
        trial = local_search(trial, cand, info, row, locked)
        if not profile_reached(trial, S):
            continue
        k = evaluate(trial, info, row)[:2]
        if best is None or k < best[0]:
            best = (k, trial)
    return best[1] if best else None


def heuristic_plans(lines, S, info, row):
    cand = candidates(lines, S)
    if cand is None:
        return []
    out = []
    for start in ("fill", "late", "early"):
        asg0 = greedy_fill(lines, cand) if start == "fill" else {i: (c[-1] if start == "late" else c[0]) for i, c in cand.items()}
        asg = local_search(asg0, cand, info, row, set())
        if not profile_reached(asg, S):
            asg = repair_profile(asg, cand, info, row, S)
            if asg is None:
                continue
        out.append(asg)
    return out


# ------------------------------------------------------------------ source b : programmation dynamique exacte
def dp_plans(lines, S, info, row):
    """Groupes de jours de besoin consécutifs livrés ensemble ; « profil atteint » imposé par un masque."""
    full = [x for x in lines if x[1] >= CAP_KG - 1e-6 or x[2] >= CAP_M3 - 1e-6]   # un camion à elles seules
    small = [x for x in lines if x not in full]
    allowed = [d for d in OPEN_DAYS if d.weekday() in S]
    by_day = defaultdict(list)
    for t, w, v, i in small:
        by_day[t].append((w, v, i))
    dates = sorted(by_day); n = len(dates)
    cost, window = {}, {}
    for i in range(n):
        for j in range(i, n):
            if (dates[j] - dates[i]).days > ANTICIP:
                break
            cost[i, j] = pack_cost([x for d in dates[i:j + 1] for x in by_day[d]], row)[0]
            window[i, j] = [d for d in allowed if dates[j] - dt.timedelta(days=ANTICIP) <= d <= dates[i]]
    best = None
    for w in FULL_WEEKS:
        targets = [d for d in OPEN_DAYS if week_key(d) == w and d.weekday() in S]
        if len(targets) < len(S):
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
                    for k, m2 in [(None, mask)] + [(k, mask | 1 << k) for k, t in enumerate(targets)
                                                   if not mask >> k & 1 and t in window[i, j]]:
                        nxt = f[j + 1][m2]
                        if nxt is None:
                            continue
                        key = (cost[i, j] + nxt[0][0], 1 + nxt[0][1])
                        if cb is None or key < cb[0]:
                            cb = (key, [(i, j, k)] + nxt[1])
                f[i][mask] = cb
        if f[0][0] is not None and (best is None or f[0][0][0] < best[0][0]):
            best = (f[0][0], targets)
    if best is None:
        if n or len(S) > 1:
            return []
        best = (((0.0, 0), []), [])
    (_, groups), targets = best
    out = []
    for variant in (-1, 0):                                   # au plus tard, au plus tôt
        asg = {}
        for i, j, k in groups:
            day = targets[k] if k is not None else window[i, j][variant]
            for d in dates[i:j + 1]:
                for _, _, lid in by_day[d]:
                    asg[lid] = day
        days_used = set(asg.values())
        ok = True
        for t, w, v, lid in full:                             # sur un jour de livraison déjà prévu si possible
            c = [d for d in days_used if t - dt.timedelta(days=ANTICIP) <= d <= t] or \
                [d for d in allowed if t - dt.timedelta(days=ANTICIP) <= d <= t]
            if not c:
                ok = False; break
            asg[lid] = max(c); days_used.add(asg[lid])
        if ok and profile_reached(asg, S):
            out.append(asg)
    return out


# ------------------------------------------------------------------ lissage par usine (CP-SAT)
def smooth_plant(keys, alts, first):
    base_cost = sum(alts[k][first[k]]["cost"] for k in keys)
    days = sorted({d for k in keys for a in alts[k] for d in a["per_day"]})

    def solve(peak=None):
        m = cp_model.CpModel()
        b = {(k, i): m.NewBoolVar("") for k in keys for i in range(len(alts[k]))}
        for k in keys:
            m.AddExactlyOne(b[k, i] for i in range(len(alts[k])))
        P = m.NewIntVar(0, 10_000, "pic")
        for d in days:
            m.Add(sum(a["per_day"].get(d, 0) * b[k, i] for k in keys for i, a in enumerate(alts[k])) <= P)
        cost = sum(round(a["cost"] * 100) * b[k, i] for k in keys for i, a in enumerate(alts[k]))
        trucks = sum(a["trucks"] * b[k, i] for k in keys for i, a in enumerate(alts[k]))
        m.Add(cost <= round(base_cost * 100 * (1 + LISSAGE_MAX)))            # règle 28
        if peak is None:
            m.Minimize(P)                                                    # règle 27
        else:
            m.Add(P <= peak); m.Minimize(trucks * 10**9 + cost)              # règle 29, puis coût
        s = cp_model.CpSolver(); s.parameters.max_time_in_seconds = SMOOTH_TIME
        st = s.Solve(m)
        return s, st, b, P

    s, st, b, P = solve(); peak = s.Value(P)
    s, st2, b, P = solve(peak)
    choice = {k: next(i for i in range(len(alts[k])) if s.Value(b[k, i])) for k in keys}
    return choice, peak, s.StatusName(st2)


# ------------------------------------------------------------------ main
def main(schedule_path, dist_path, out_dir="sorties"):
    d, log = load(schedule_path, dist_path)
    info = {r.line_id: (r.weight, r.volume) for r in d.itertuples()}
    couples = sorted(d.groupby(["supplier_id", "plant"]).groups.keys())
    rows, base = {}, {}

    # situation actuelle : livraison le jour du besoin
    for (s, p), g in d.groupby(["supplier_id", "plant"]):
        row = row_of(g["distance"].iloc[0]); rows[s, p] = row
        cost = trucks = 0
        for _, gg in g.groupby("date"):
            c, bns = pack(list(zip(gg.weight, gg.volume, gg.line_id)), row)
            cost += c; trucks += len(bns)
        dates = sorted(g["date"].unique())
        per_week = pd.Series([week_key(x) for x in dates]).value_counts()
        per_month = pd.Series([(x.year, x.month) for x in dates]).value_counts()
        base[s, p] = dict(cost=cost, trucks=trucks, deliveries=len(dates),
                          profile="1 / mois" if per_month.max() <= 1 else f"{per_week.max()} / semaine")   # règle 26

    # plans des deux sources, pour chaque jeu de jours autorisés
    alts = {}
    for k, g in d.groupby(["supplier_id", "plant"]):
        lines = list(zip(g.date, g.weight, g.volume, g.line_id)); row = rows[k]
        res, seen = [], set()
        for S in SUBSETS:
            for src, plans in (("heuristique", heuristic_plans(lines, S, info, row)), ("dp", dp_plans(lines, S, info, row))):
                for asg in plans:
                    sig = (S, frozenset(asg.items()))
                    if sig in seen:
                        continue
                    seen.add(sig)
                    cost, ndel, ntr, per_day = evaluate(asg, info, row)
                    res.append(dict(S=S, asg=asg, cost=cost, deliveries=ndel, trucks=ntr, per_day=per_day, source=src))
        res.sort(key=lambda a: (round(a["cost"], 2), a["deliveries"], len(a["S"]), a["S"]))   # règles 30-31
        alts[k] = res
    first = {k: 0 for k in alts}
    log["plans_generes"] = sum(map(len, alts.values()))
    log["temps_plans_s"] = round(time.time() - T0, 1)
    trucks_before_smoothing = sum(alts[k][0]["trucks"] for k in alts)
    cost_before_smoothing = sum(alts[k][0]["cost"] for k in alts)

    # lissage par usine
    choice, lissage_log = {}, []
    for plant in sorted({k[1] for k in alts}):
        ks = [k for k in alts if k[1] == plant]
        cnt = defaultdict(int)
        for k in ks:
            for dd, n in alts[k][0]["per_day"].items():
                cnt[dd] += n
        ch, peak, status = smooth_plant(ks, alts, first)
        choice.update(ch)
        opt_cost = sum(alts[k][0]["cost"] for k in ks)
        extra = sum(alts[k][ch[k]]["cost"] for k in ks) - opt_cost
        lissage_log.append(dict(usine=plant, cout_optimise=round(opt_cost, 2), surcout_lissage=round(extra, 2),
                                surcout_pct=round(100 * extra / opt_cost, 3), pic_avant_lissage=max(cnt.values()),
                                pic_apres_lissage=peak, statut=status))
    log["temps_lissage_s"] = round(time.time() - T0, 1)

    # amélioration locale ligne par ligne (pic et profil conservés)
    asg_all = {}
    for k in alts:
        asg_all.update({i: (k, dd) for i, dd in alts[k][choice[k]]["asg"].items()})
    days = defaultdict(lambda: defaultdict(list))
    for i, (k, dd) in asg_all.items():
        days[k][dd].append((info[i][0], info[i][1], i))
    cache = {(k, dd): pack_cost(it, rows[k]) for k in days for dd, it in days[k].items()}
    load_pd = defaultdict(int)
    for (k, dd), (_, n) in cache.items():
        load_pd[k[1], dd] += n
    peak_of = {l["usine"]: l["pic_apres_lissage"] for l in lissage_log}
    need_of = dict(zip(d.line_id, d.date))
    gain, improved, passes = 0.0, True, 0
    while improved and passes < 6:
        improved, passes = False, passes + 1
        for k, dd in days.items():
            S = alts[k][choice[k]]["S"]
            for d_from in list(dd):
                for item in list(dd.get(d_from, [])):
                    t = need_of[item[2]]
                    for d_to in list(dd):
                        if d_to == d_from or not t - dt.timedelta(days=ANTICIP) <= d_to <= t:
                            continue
                        a = [x for x in dd[d_from] if x[2] != item[2]]; b = dd[d_to] + [item]
                        (ca, na), (cb, nb) = pack_cost(a, rows[k]), pack_cost(b, rows[k])
                        (coa, oa), (cob, ob) = cache[k, d_from], cache[k, d_to]
                        if na + nb > oa + ob or ca + cb > coa + cob - 0.01:
                            continue
                        if load_pd[k[1], d_to] + nb - ob > peak_of[k[1]]:
                            continue
                        trial = {x[2]: day for day, its in dd.items() for x in its}
                        trial[item[2]] = d_to
                        if not profile_reached(trial, S):
                            continue
                        dd[d_from], dd[d_to] = a, b
                        cache[k, d_from], cache[k, d_to] = (ca, na), (cb, nb)
                        load_pd[k[1], d_from] += na - oa; load_pd[k[1], d_to] += nb - ob
                        gain += coa + cob - ca - cb; improved = True
                        break
                if d_from in dd and not dd[d_from]:
                    del dd[d_from]
    log["gain_amelioration_locale_eur"] = round(gain, 2)

    # ---------- sorties
    lines_df = d.set_index("line_id")
    out1, out2, out3, tid = [], [], [], 0
    for k in couples:
        s, p = k; row = rows[k]; S = alts[k][choice[k]]["S"]
        cost_after, tw, n_trucks = 0.0, [], 0
        for dd in sorted(days[k]):
            for bw, bv, ids in pack(days[k][dd], row)[1]:
                tid += 1; n_trucks += 1; price = truck_price(row, bw); cost_after += price; tw.append(bw)
                for lid in ids:
                    L = lines_df.loc[lid]
                    out2.append(dict(camion_id=f"T{tid:05d}", line_id=int(lid), supplier_id=s, plant=p, date_livraison=dd,
                                     jour=JOURS[dd.weekday()], material=L.material, unloading_point=L.unloading_point,
                                     date_besoin=L.date, anticipation_j=(L.date - dd).days,
                                     schedule_quantity=L.schedule_quantity, weight=L.weight, volume=L.volume,
                                     donnee_manquante=bool(L.weight_missing), poids_camion=round(bw, 2),
                                     volume_camion=round(bv, 3), remplissage_poids_pct=round(100 * bw / CAP_KG, 1),
                                     remplissage_volume_pct=round(100 * bv / CAP_M3, 1), prix_camion_eur=round(price, 2)))
        B = base[k]; nweeks = len({week_key(x) for x in days[k]})
        out1.append(dict(supplier_id=s, plant=p, distance_km=lines_df[lines_df.supplier_id == s].distance.iloc[0],
                         profil_avant=B["profile"], livraisons_avant=B["deliveries"],
                         frequence_cible=f"{len(S)} / semaine", jours_autorises=", ".join(JOURS[x] for x in S),
                         livraisons_apres=len(days[k]), camions_apres=n_trucks,
                         camions_par_semaine_moy=round(n_trucks / max(nweeks, 1), 2),
                         remplissage_poids_moy_pct=round(100 * sum(tw) / (CAP_KG * len(tw)), 1),
                         source_du_plan=alts[k][choice[k]]["source"]))
        out3.append(dict(supplier_id=s, plant=p, cout_avant_eur=round(B["cost"], 2), camions_avant=B["trucks"],
                         cout_apres_eur=round(cost_after, 2), camions_apres=n_trucks,
                         saving_eur=round(B["cost"] - cost_after, 2),
                         saving_pct=round(100 * (B["cost"] - cost_after) / B["cost"], 1)))
    o1, o2, o3 = pd.DataFrame(out1), pd.DataFrame(out2), pd.DataFrame(out3).sort_values("saving_eur", ascending=False)

    # ---------- contrôles
    checks = []
    def chk(name, ok): checks.append(dict(controle=name, ok=bool(ok)))
    prof = o1.set_index(["supplier_id", "plant"])
    dl = o2.drop_duplicates(["supplier_id", "plant", "date_livraison"]).copy()
    dl["wk"] = [week_key(x) for x in dl.date_livraison]
    perwk = dl.groupby(["supplier_id", "plant", "wk"]).size()
    ntrucks = o2.camion_id.nunique()
    total_before, total_after = o3.cout_avant_eur.sum(), o3.cout_apres_eur.sum()
    cal = o2.drop_duplicates("camion_id").pivot_table(index="date_livraison", columns="plant", values="camion_id",
                                                      aggfunc="count", fill_value=0)
    chk("Chaque besoin livré une seule fois (règle 10)", o2.line_id.is_unique and set(o2.line_id) == set(d.line_id))
    chk("Jamais de retard (règle 11)", (o2.anticipation_j >= 0).all())
    chk("Anticipation <= 7 j (règle 12)", (o2.anticipation_j <= ANTICIP).all())
    chk("Pas de week-end ni de férié (règles 7-8)", all(x.weekday() < 5 and x not in HOL for x in o2.date_livraison))
    chk("Livraison >= 02/11 (règle 9)", (o2.date_livraison >= D_START).all())
    chk("Capacité poids et volume (règle 13)", (o2.poids_camion <= CAP_KG + 1e-6).all() and (o2.volume_camion <= CAP_M3 + 1e-6).all())
    chk("1 camion = 1 couple (règle 14)", (o2.groupby("camion_id")[["supplier_id", "plant"]].nunique() == 1).all().all())
    chk("Ligne jamais coupée (règle 16)", o2.line_id.is_unique)
    chk("Poids et volume conservés", abs(o2.weight.sum() - d.weight.sum()) < 1e-6 and abs(o2.volume.sum() - d.volume.sum()) < 1e-6)
    chk("Livraison uniquement les jours autorisés (règle 23)", all(
        JOURS[x.date_livraison.weekday()] in prof.loc[(x.supplier_id, x.plant), "jours_autorises"] for x in dl.itertuples()))
    r24 = True
    for (s_, p_), g in dl.groupby(["supplier_id", "plant"]):
        S_ = {JOURS.index(j) for j in prof.loc[(s_, p_), "jours_autorises"].split(", ")}
        r24 &= any(set(gg.date_livraison.map(lambda x: x.weekday())) >= S_ for w, gg in g.groupby("wk") if w in FULL_WEEKS)
    chk("Profil atteint sur une semaine complète (règle 24)", r24)
    chk("Livraisons par semaine <= profil (règle 25)", all(n <= int(prof.loc[(s_, p_), "frequence_cible"][0]) for (s_, p_, _), n in perwk.items()))
    chk("Lissage <= 1 % par usine (règle 28)", all(l["surcout_pct"] <= 100 * LISSAGE_MAX + 1e-9 for l in lissage_log))
    chk("Lissage sans camion ajouté (règle 29)", ntrucks <= trucks_before_smoothing)
    runtime = time.time() - T0
    chk("Saving >= 40 000 EUR", total_before - total_after >= 40000)
    chk("Moins de 800 camions", ntrucks < 800)
    chk("Exécution < 90 s", runtime < 90)

    synth = pd.DataFrame([
        ("Couples fournisseur x usine", len(couples)),
        ("Lignes de besoin retenues", len(d)),
        ("Livraisons avant (couple x jour)", int(o1.livraisons_avant.sum())),
        ("Livraisons après", int(o1.livraisons_apres.sum())),
        ("Camions avant", int(o3.camions_avant.sum())),
        ("Camions avant lissage", trucks_before_smoothing),
        ("Camions après", ntrucks),
        ("dont camions 'plein' imposés par données manquantes", int(o2[o2.donnee_manquante].camion_id.nunique())),
        ("Coût avant (EUR)", round(total_before, 2)),
        ("Coût avant lissage (EUR)", round(cost_before_smoothing, 2)),
        ("Coût après (EUR)", round(total_after, 2)),
        ("Saving (EUR)", round(total_before - total_after, 2)),
        ("Saving (%)", round(100 * (total_before - total_after) / total_before, 2)),
        ("Pics PLT_001/002/003/004 (camions/jour)", "/".join(str(int(cal[c].max())) for c in sorted(cal.columns))),
        ("Remplissage poids moyen après (%)", round(100 * o2.drop_duplicates("camion_id").poids_camion.mean() / CAP_KG, 1)),
        ("Temps d'exécution (s)", round(runtime, 1)),
    ], columns=["indicateur", "valeur"])

    os.makedirs(out_dir, exist_ok=True)
    o1.to_csv(f"{out_dir}/sortie1_profils.csv", index=False)
    o2.to_csv(f"{out_dir}/sortie2_camions.csv", index=False)
    o3.to_csv(f"{out_dir}/sortie3_saving.csv", index=False)
    with pd.ExcelWriter(f"{out_dir}/resultats_consolidation.xlsx") as xw:
        synth.to_excel(xw, sheet_name="Synthese", index=False)
        o1.to_excel(xw, sheet_name="Sortie1_Profils", index=False)
        o2.to_excel(xw, sheet_name="Sortie2_Camions", index=False)
        o3.to_excel(xw, sheet_name="Sortie3_Saving", index=False)
        pd.DataFrame(lissage_log).to_excel(xw, sheet_name="Lissage", index=False)
        cal.to_excel(xw, sheet_name="Camions_par_jour")
        pd.DataFrame(list(log.items()), columns=["etape", "valeur"]).to_excel(xw, sheet_name="Preparation", index=False)
        pd.DataFrame(checks).to_excel(xw, sheet_name="Controles", index=False)
    print(synth.to_string(index=False))
    print(pd.DataFrame(lissage_log).to_string(index=False))
    print(pd.DataFrame(checks).to_string(index=False))
    print(pd.DataFrame(list(log.items()), columns=["etape", "valeur"]).to_string(index=False))
    return synth


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], a[1], a[2] if len(a) > 2 else "sorties")
