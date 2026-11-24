"""Orchestration: baseline, validation, plans, smoothing, local improvement, exact loading, controls, outputs."""
from __future__ import annotations

import itertools
import os
from concurrent.futures import ProcessPoolExecutor
import time
from collections import defaultdict

import pandas as pd

from .config import Config
from .data import Calendar, build_calendar, load_needs, load_rate_sheet
from .loading import Loader
from .plans import Planner
from .smoothing import smooth_plant
from .polish import polish_couple

DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
_W: dict = {}


def _init_worker(cfg: Config, rates, cal) -> None:
    """One planner per process (its packing cache stays local to the process)."""
    _W["cfg"] = cfg
    _W["planner"] = Planner(cal, Loader(cfg, rates), cfg.anticipation_days, variant_tolerance=cfg.plan_variant_tolerance)


def _init_polish(cfg, rates, cal) -> None:
    _W.update(cfg=cfg, rates=rates, cal=cal)


def _polish(task):
    k, row, lines, plan, S = task
    return k, polish_couple(_W["cfg"], _W["rates"], _W["cal"], row, lines, plan, S)


def _couple_plans(task) -> list[dict]:
    """Every candidate plan of one couple, sorted cheapest first (rules 30-31). Deterministic: same input, same list."""
    k, lines, row = task
    cfg, planner = _W["cfg"], _W["planner"]
    info = {i: (w, v) for _, w, v, i in lines}
    res, seen = [], set()
    for S in (c for n in cfg.frequencies for c in itertools.combinations(range(5), n)):
        for source, plans in (("heuristique", planner.heuristic(lines, S, info, row)),
                              ("programmation dynamique", planner.dynamic(lines, S, info, row))):
            for asg in plans:
                sig = (S, frozenset(asg.items()))
                if sig not in seen:
                    seen.add(sig)
                    cost, ndel, ntr, per_day = planner.evaluate(asg, info, row)
                    res.append(dict(S=S, asg=asg, cost=cost, deliveries=ndel, trucks=ntr, per_day=per_day, source=source))
    res.sort(key=lambda a: (round(a["cost"], 2), a["deliveries"], len(a["S"]), a["S"], a["source"]))
    return res


def run(cfg: Config = Config()) -> dict:
    t0 = time.perf_counter()
    rates = load_rate_sheet(cfg)
    needs, log = load_needs(cfg)
    cal = build_calendar(cfg, needs)
    loader = Loader(cfg, rates)
    info = {r.line_id: (r.weight, r.volume) for r in needs.itertuples()}
    groups = {k: g for k, g in needs.groupby(["supplier_id", "plant"])}
    rows = {k: rates.row_of(g["distance"].iloc[0]) for k, g in groups.items()}
    lines = {k: list(zip(g.date, g.weight, g.volume, g.line_id)) for k, g in groups.items()}

    # ---------- current situation: every line on its need day (milestone 2)
    base = {}
    for k, g in groups.items():
        cost = trucks = 0
        for _, gg in g.groupby("date"):
            c, n = loader.cost(list(zip(gg.weight, gg.volume, gg.line_id)), rows[k])
            cost += c; trucks += n
        dates = sorted(g["date"].unique())
        per_week = pd.Series([Calendar.week_key(x) for x in dates]).value_counts()
        per_month = pd.Series([(x.year, x.month) for x in dates]).value_counts()
        base[k] = dict(cost=cost, trucks=trucks, deliveries=len(dates),
                       profile="1 / mois" if per_month.max() <= 1 else f"{per_week.max()} / semaine")   # rule 26

    # ---------- consistency with milestone 2: its estimator (divisible load) on the same deliveries
    est = 0.0
    for k, g in groups.items():
        for _, gg in g.groupby("date"):
            w, v = gg.weight.sum(), gg.volume.sum()
            fill = max(w / cfg.truck_kg, v / cfg.truck_m3)
            n = max(1, -(-fill // 1))
            full_w = w / fill if fill > 1 else w
            est += (n - 1) * rates.price(rows[k], full_w) + rates.price(rows[k], w - (n - 1) * full_w)
    log["cout_actuel_estimateur_M2_eur"] = round(est, 2)
    if cfg.m2_baseline_eur is not None and abs(est - cfg.m2_baseline_eur) > 0.05:
        raise AssertionError(f"not the milestone 2 data: {est:.2f} EUR instead of {cfg.m2_baseline_eur:.2f} EUR")

    # ---------- validation: with a 0-day window and every weekday allowed, the engine must find the current situation
    zero = Planner(cal, loader, anticipation=0, require_profile=False)
    for k in groups:
        plan = zero.dynamic(lines[k], (0, 1, 2, 3, 4), info, rows[k])[0]
        cost, ndel, ntr, _ = zero.evaluate(plan, info, rows[k])
        if not (abs(cost - base[k]["cost"]) < 1e-6 and ndel == base[k]["deliveries"] and ntr == base[k]["trucks"]):
            raise AssertionError(f"window 0: engine does not reproduce the current situation for {k}")
    log["validation_fenetre_0"] = "OK : les 1 665 livraisons et le coût actuel sont retrouvés"

    # ---------- plans of the two sources for every set of allowed weekdays (rules 22-25), couples in parallel
    tasks = sorted(((k, lines[k], rows[k]) for k in groups), key=lambda t: (-len(t[1]), t[0]))   # big couples first
    with ProcessPoolExecutor(max_workers=cfg.workers or os.cpu_count(), initializer=_init_worker,
                             initargs=(cfg, rates, cal)) as pool:
        alts = dict(zip([t[0] for t in tasks], pool.map(_couple_plans, tasks, chunksize=1)))
    planner = Planner(cal, loader, cfg.anticipation_days)
    log["plans_generes"] = sum(map(len, alts.values()))
    before = {"cost": sum(alts[k][0]["cost"] for k in alts), "trucks": sum(alts[k][0]["trucks"] for k in alts),
              "deliveries": sum(alts[k][0]["deliveries"] for k in alts)}

    # ---------- smoothing per plant (rules 27-29)
    choice, smooth_log = {}, []
    for plant in sorted({k[1] for k in alts}):
        keys = [k for k in sorted(alts) if k[1] == plant]
        ch, status = smooth_plant(cfg, keys, alts)
        choice.update(ch)
        load0 = defaultdict(int)
        for k in keys:
            for d, n in alts[k][0]["per_day"].items():
                load0[d] += n
        ref = sum(alts[k][0]["cost"] for k in keys)
        extra = sum(alts[k][ch[k]]["cost"] for k in keys) - ref
        smooth_log.append(dict(usine=plant, cout_avant_lissage=round(ref, 2), surcout_lissage=round(extra, 2),
                               surcout_pct=round(100 * extra / ref, 3), pic_avant_lissage=max(load0.values()),
                               pic_apres_lissage=status["pic"], couples_qui_changent_de_profil=sum(
                                   alts[k][ch[k]]["S"] != alts[k][0]["S"] for k in keys),
                               statut_solveur=f"{status['statut_pic']} / {status['statut_cout']}"))

    # ---------- local improvement, line by line (peak and profile preserved)
    days = {k: defaultdict(list) for k in alts}
    for k in alts:
        for i, d in alts[k][choice[k]]["asg"].items():
            days[k][d].append((info[i][0], info[i][1], i))
    cache = {(k, d): loader.cost(it, rows[k]) for k in days for d, it in days[k].items()}
    plant_load = defaultdict(int)
    for (k, d), (_, n) in cache.items():
        plant_load[k[1], d] += n
    peak = {s["usine"]: s["pic_apres_lissage"] for s in smooth_log}
    need = dict(zip(needs.line_id, needs.date))
    gain, improved, passes = 0.0, True, 0
    while improved and passes < 6:
        improved, passes = False, passes + 1
        for k in sorted(days):
            dd, S = days[k], alts[k][choice[k]]["S"]
            for d_from in sorted(dd):
                for item in sorted(dd.get(d_from, []), key=lambda x: x[2]):
                    t = need[item[2]]
                    for d_to in sorted(dd):
                        if d_to == d_from or not t - planner.H <= d_to <= t:
                            continue
                        a = [x for x in dd[d_from] if x[2] != item[2]]; b = dd[d_to] + [item]
                        (ca, na), (cb, nb) = loader.cost(a, rows[k]), loader.cost(b, rows[k])
                        (coa, oa), (cob, ob) = cache[k, d_from], cache[k, d_to]
                        if na + nb > oa + ob or ca + cb > coa + cob - 0.01 or plant_load[k[1], d_to] + nb - ob > peak[k[1]]:
                            continue
                        trial = {x[2]: day for day, its in dd.items() for x in its}
                        trial[item[2]] = d_to
                        if not planner.profile_reached(trial, S):
                            continue
                        dd[d_from], dd[d_to] = a, b
                        cache[k, d_from], cache[k, d_to] = (ca, na), (cb, nb)
                        plant_load[k[1], d_from] += na - oa; plant_load[k[1], d_to] += nb - ob
                        gain += coa + cob - ca - cb; improved = True
                        break
                if d_from in dd and not dd[d_from]:
                    del dd[d_from]
    log["gain_amelioration_locale_eur"] = round(gain, 2)

    # ---------- exact re-optimisation of each couple, line by line (same days, no extra truck per day)
    tasks = [(k, rows[k], lines[k], {x[2]: d for d, its in days[k].items() for x in its}, alts[k][choice[k]]["S"])
             for k in sorted(days)]
    with ProcessPoolExecutor(max_workers=cfg.workers or os.cpu_count(), initializer=_init_polish,
                             initargs=(cfg, rates, cal)) as pool:
        polished = {k: r for k, r in pool.map(_polish, tasks, chunksize=4) if r is not None}
    log["couples_ameliores_par_le_modele_exact"] = len(polished)

    # ---------- exact loading of the final plan and outputs
    L = needs.set_index("line_id")
    out1, out2, out3, tid, exact_gain = [], [], [], 0, 0.0
    for k in sorted(alts):
        s, p = k; row = rows[k]; a = alts[k][choice[k]]; S = a["S"]; S0 = alts[k][0]["S"]
        cost_after, weights = 0.0, []
        if k in polished:                                   # trucks come from the exact model
            groups_ = defaultdict(list)
            for lid, (d, j) in polished[k][0].items():
                groups_[d, j].append(lid)
            truck_list = [(d, [sum(info[i][0] for i in ids), sum(info[i][1] for i in ids), ids])
                          for (d, j), ids in sorted(groups_.items())]
            days[k] = defaultdict(list)
            for d, b in truck_list:
                days[k][d].extend((info[i][0], info[i][1], i) for i in b[2])
        else:
            truck_list = []
            for d in sorted(days[k]):
                bins = loader.exact(days[k][d], row)
                exact_gain += loader.cost(days[k][d], row)[0] - sum(rates.price(row, b[0]) for b in bins)
                truck_list += [(d, b) for b in bins]
        for d, (bw, bv, ids) in truck_list:
                tid += 1; price = rates.price(row, bw); cost_after += price; weights.append(bw)
                for lid in sorted(ids):
                    r = L.loc[lid]
                    out2.append(dict(camion_id=f"T{tid:05d}", line_id=int(lid), supplier_id=s, plant=p, date_livraison=d,
                                     jour=DAYS[d.weekday()], material=r.material, unloading_point=r.unloading_point,
                                     date_besoin=r.date, anticipation_j=(r.date - d).days, schedule_quantity=r.schedule_quantity,
                                     weight=r.weight, volume=r.volume, donnee_manquante=bool(r.load_missing),
                                     poids_camion=round(bw, 2), volume_camion=round(bv, 3),
                                     remplissage_poids_pct=round(100 * bw / cfg.truck_kg, 1),
                                     remplissage_volume_pct=round(100 * bv / cfg.truck_m3, 1), prix_camion_eur=round(price, 2)))
        B = base[k]; n_weeks = len({Calendar.week_key(x) for x in days[k]})
        out1.append(dict(supplier_id=s, plant=p, distance_km=L.loc[L.supplier_id == s, "distance"].iloc[0],
                         profil_avant=B["profile"], livraisons_avant=B["deliveries"],
                         frequence_cible=f"{len(S)} / semaine", jours_autorises=", ".join(DAYS[x] for x in S),
                         jours_autorises_avant_lissage=", ".join(DAYS[x] for x in S0), profil_change_au_lissage=S != S0,
                         livraisons_apres=len(days[k]), camions_apres=len(weights),
                         camions_par_semaine_moy=round(len(weights) / max(n_weeks, 1), 2),
                         remplissage_poids_moy_pct=round(100 * sum(weights) / (cfg.truck_kg * len(weights)), 1),
                         source_du_plan=a["source"]))
        out3.append(dict(supplier_id=s, plant=p, cout_avant_eur=round(B["cost"], 2), camions_avant=B["trucks"],
                         cout_apres_eur=round(cost_after, 2), camions_apres=len(weights),
                         saving_eur=round(B["cost"] - cost_after, 2),
                         saving_pct=round(100 * (B["cost"] - cost_after) / B["cost"], 1)))
    log["gain_chargement_exact_eur (couples non réoptimisés)"] = round(exact_gain, 2)
    o1, o2 = pd.DataFrame(out1), pd.DataFrame(out2)
    o3 = pd.DataFrame(out3).sort_values("saving_eur", ascending=False)
    runtime = time.perf_counter() - t0
    return dict(cfg=cfg, needs=needs, cal=cal, log=log, o1=o1, o2=o2, o3=o3, smooth_log=smooth_log,
                before_smoothing=before, runtime=runtime)
