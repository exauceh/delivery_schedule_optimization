"""Data shown by the web interface, built from the output tables of a finished run (no computation of the engine).

Each function returns a pydantic model: the JSON contract between the API (`api.routes.views`) and the interface
(`web/app.js`). The tables are read once per run and kept in memory: a finished run never changes.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import BaseModel

from ..io.schemas import OutputName
from ..pricing.tariff import Tariff
from ..storage.base import RunManifest

logger = logging.getLogger(__name__)

DATES = {
    OutputName.TRUCKS: ["date"],
    OutputName.ALLOCATIONS: ["delivery_date", "need_date", "profile_day"],
    OutputName.TRUCK_CONTENTS: ["delivery_date"],
    OutputName.CURRENT_DELIVERIES: ["date"],
    OutputName.CURRENT_TRUCKS: ["date"],
}


@lru_cache(maxsize=32)
def _read(output_dir: str, name: OutputName) -> pd.DataFrame:
    path = Path(output_dir) / f"{name}.csv"
    logger.debug("Reading %s", path)
    return pd.read_csv(path, parse_dates=DATES.get(name, []))


def table(output_dir: Path, name: OutputName) -> pd.DataFrame:
    """One output table of a finished run (cached; callers must not modify it)."""
    return _read(str(output_dir), name)


def strs(frame: pd.DataFrame, column: str) -> list[str]:
    return [str(v) for v in frame[column].tolist()]


def floats(frame: pd.DataFrame, column: str) -> list[float]:
    return [float(v) for v in frame[column].to_numpy(dtype=float)]


def ints(frame: pd.DataFrame, column: str) -> list[int]:
    return [int(v) for v in frame[column].to_numpy(dtype=np.int64)]


def bools(frame: pd.DataFrame, column: str) -> list[bool]:
    return [bool(v) for v in frame[column].to_numpy(dtype=bool)]


def days_iso(frame: pd.DataFrame, column: str) -> list[str]:
    return [d.date().isoformat() for d in pd.DatetimeIndex(frame[column])]


def thousands(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


# --------------------------------------------------------------------------- shared pieces
class Calendar(BaseModel):
    """Trucks received per plant and per working day (rows = plants, columns = days)."""

    plants: list[str]
    days: list[str]
    trucks: list[list[int]]


def calendar(frame: pd.DataFrame, date_column: str, trucks_column: str | None = None) -> Calendar:
    """Trucks per plant and working day; `trucks_column` counts the trucks of a row (one truck per row otherwise)."""
    dates = frame[date_column]
    days = [d.date() for d in pd.bdate_range(dates.min(), dates.max())]
    counts = frame.groupby(["plant", dates.dt.date])[trucks_column].sum() if trucks_column else frame.groupby(["plant", dates.dt.date]).size()
    plants = sorted(frame["plant"].unique())
    return Calendar(plants=plants, days=[d.isoformat() for d in days],
                    trucks=[[int(counts.get((p, d), 0)) for d in days] for p in plants])


def per_week(n: int) -> str:
    return f"{n} / semaine"


def parameters_of(manifest: RunManifest) -> dict[str, object]:
    planner = manifest.parameters.get("planner")
    return dict(planner) if isinstance(planner, dict) else {}


# --------------------------------------------------------------------------- summary (Synthèse, Planning, Profils)
class SummaryKpi(BaseModel):
    cost_before: float
    cost_after: float
    saving: float
    saving_pct: float
    trucks_before: int
    trucks_after: int
    deliveries_before: int
    deliveries_after: int
    couples: int
    lines: int
    first_need: str
    last_need: str
    runtime_s: float
    imputed_trucks: int
    frequency_changes: int
    weekday_changes: int
    fill_mean_pct: float


class Control(BaseModel):
    rule: str
    what: str
    ok: bool


class OutputTest(BaseModel):
    suite: str
    test: str
    ok: bool
    detail: str


class PlantSmoothing(BaseModel):
    plant: str
    peak_before: int
    peak_after: int
    extra_cost: float


class PlantResult(BaseModel):
    plant: str
    cost_before: float
    cost_after: float
    trucks_before: int
    trucks_after: int
    peak: int


class ProfileCount(BaseModel):
    profile: str
    before: int
    after: int


class TopSaving(BaseModel):
    supplier: str
    plant: str
    saving: float
    pct: float
    before: float
    after: float


class AdvanceCount(BaseModel):
    days: int
    lines: int


class JournalRow(BaseModel):
    step: str
    value: str


class Filters(BaseModel):
    plants: list[str]
    suppliers: list[str]
    days: list[str]


class Summary(BaseModel):
    kpi: SummaryKpi
    controls: list[Control]
    output_tests: list[OutputTest]
    smoothing: list[PlantSmoothing]
    journal: list[JournalRow]
    plants: list[PlantResult]
    calendar: Calendar
    advance: list[AdvanceCount]
    profiles: list[ProfileCount]
    top_savings: list[TopSaving]
    filters: Filters


STAGES = {"preparation": "Préparation des données", "current_situation": "Situation actuelle", "candidate_plans": "Plans candidats",
          "profiles_and_smoothing": "Choix des profils et lissage", "outputs": "Sorties", "checks": "Contrôles et tests"}


def journal(manifest: RunManifest, couples: int, lines: int) -> list[JournalRow]:
    rows = [JournalRow(step="Lignes de besoin planifiées", value=thousands(lines)),
            JournalRow(step="Couples fournisseur × usine", value=thousands(couples))]
    accuracy = manifest.accuracy
    if "rows_modified" in accuracy:
        rows.append(JournalRow(step="Lignes complétées (poids, volume ou distance manquants)", value=thousands(accuracy["rows_modified"])))
    sheet = manifest.rate_sheet
    if sheet:
        rows.append(JournalRow(step="Grille tarifaire valable depuis", value=str(sheet.get("valid_from") or "?")))
        rows.append(JournalRow(step="Marge et surcharge diesel (RG11)", value=f"{sheet.get('margin')} et {sheet.get('diesel_surcharge')}"))
    rows += [JournalRow(step=f"Temps : {STAGES.get(stage, stage)}", value=f"{seconds:.1f} s".replace(".", ","))
             for stage, seconds in manifest.timings.items()]
    return rows


def summary(output_dir: Path, manifest: RunManifest) -> Summary:
    """Everything the synthesis, planning and profiles screens need."""
    trucks, alloc = table(output_dir, OutputName.TRUCKS), table(output_dir, OutputName.ALLOCATIONS)
    saving, profiles = table(output_dir, OutputName.SAVING), table(output_dir, OutputName.PROFILES)
    smoothing, couples = table(output_dir, OutputName.SMOOTHING_PLANTS), table(output_dir, OutputName.SMOOTHING_COUPLES)
    checks, tests = table(output_dir, OutputName.CHECKS), table(output_dir, OutputName.OUTPUT_TESTS)

    cal = calendar(trucks, "date")
    cost_before, cost_after = float(saving["cost_before"].sum()), float(saving["cost_after"].sum())
    by_plant = saving.groupby("plant").agg(cost_before=("cost_before", "sum"), cost_after=("cost_after", "sum"),
                                           trucks_before=("trucks_before", "sum"), trucks_after=("trucks_after", "sum")).reset_index()
    labels = sorted(set(profiles["current_profile"]) | set(profiles["profile"]))
    before_counts, after_counts = profiles["current_profile"].value_counts(), profiles["profile"].value_counts()
    top = saving.nlargest(15, "saving")
    advance = alloc.groupby("advance_days").size().rename("lines").reset_index()
    kpi = SummaryKpi(
        cost_before=cost_before, cost_after=cost_after, saving=cost_before - cost_after,
        saving_pct=100 * (cost_before - cost_after) / cost_before if cost_before else 0.0,
        trucks_before=int(saving["trucks_before"].sum()), trucks_after=len(trucks),
        deliveries_before=int(saving["deliveries_before"].sum()), deliveries_after=int(saving["deliveries_after"].sum()),
        couples=len(profiles), lines=len(alloc),
        first_need=min(days_iso(alloc, "need_date")), last_need=max(days_iso(alloc, "need_date")),
        runtime_s=sum(manifest.timings.values()), imputed_trucks=int(trucks["imputed_full_truck"].astype(bool).sum()),
        frequency_changes=int((couples["profile_before_smoothing"] != couples["profile"]).sum()),
        weekday_changes=int((couples["profile_days_before_smoothing"] != couples["profile_days"]).sum()),
        fill_mean_pct=100 * float(trucks["fill"].mean()),
    )
    return Summary(
        kpi=kpi,
        controls=[Control(rule=rule, what=what, ok=ok)
                  for rule, what, ok in zip(strs(checks, "contrôle"), strs(checks, "ce qui est vérifié"), bools(checks, "respecté"))],
        output_tests=[OutputTest(suite=suite, test=test, ok=ok, detail=detail)
                      for suite, test, ok, detail in zip(strs(tests, "suite"), strs(tests, "test"), bools(tests, "respecté"),
                                                         strs(tests, "détail"))],
        smoothing=[PlantSmoothing(plant=plant, peak_before=before, peak_after=after, extra_cost=cost_after - cost_before)
                   for plant, before, after, cost_before, cost_after in zip(
                       strs(smoothing, "plant"), ints(smoothing, "peak_before_smoothing"), ints(smoothing, "peak_after_smoothing"),
                       floats(smoothing, "cost_before_smoothing"), floats(smoothing, "cost_after_smoothing"))],
        journal=journal(manifest, len(profiles), len(alloc)),
        plants=[PlantResult(plant=plant, cost_before=cb, cost_after=ca, trucks_before=tb, trucks_after=ta,
                            peak=max(cal.trucks[cal.plants.index(plant)], default=0))
                for plant, cb, ca, tb, ta in zip(strs(by_plant, "plant"), floats(by_plant, "cost_before"), floats(by_plant, "cost_after"),
                                                 ints(by_plant, "trucks_before"), ints(by_plant, "trucks_after"))],
        calendar=cal,
        advance=[AdvanceCount(days=d, lines=n) for d, n in zip(ints(advance, "advance_days"), ints(advance, "lines"))],
        profiles=[ProfileCount(profile=per_week(int(n)), before=int(before_counts.get(n, 0)), after=int(after_counts.get(n, 0)))
                  for n in labels],
        top_savings=[TopSaving(supplier=s, plant=p, saving=v, pct=pc, before=b, after=a)
                     for s, p, v, pc, b, a in zip(strs(top, "supplier_id"), strs(top, "plant"), floats(top, "saving"),
                                                  floats(top, "saving_pct"), floats(top, "cost_before"), floats(top, "cost_after"))],
        filters=Filters(plants=cal.plants, suppliers=sorted(trucks["supplier_id"].unique()), days=cal.days),
    )


# --------------------------------------------------------------------------- current situation, anomalies, rules
class SituationKpi(BaseModel):
    first: str
    last: str
    lines: int
    deliveries: int
    trucks: int
    cost: float
    fill_pct: float


class Anomaly(BaseModel):
    rule: str
    label: str
    count: int
    treatment: str
    kind: str  # "corrige" (data corrected or removed), "info"


class Heatmap(BaseModel):
    """Current trucks on the rate sheet: rows = distance brackets, columns = weight brackets."""

    km: list[str]
    kg: list[str]
    trucks: list[list[int]]
    cost: list[list[float]]
    price: list[list[float]]


class Situation(BaseModel):
    kpi: SituationKpi
    anomalies: list[Anomaly]
    rules: list[str]
    calendar: Calendar
    heatmap: Heatmap  # current trucks on the rate sheet
    heatmap_after: Heatmap  # trucks of the plan on the same rate sheet
    fill_before: list[int]  # trucks per fill bracket of 10 % (0-10 %, ..., 90-100 %), today
    fill_after: list[int]  # same, with the plan


# (rule, part of the action written by the preparation) -> label, treatment, kind; the first match wins
ANOMALIES: list[tuple[str, str, str, str, str]] = [
    ("DQ01", "dropped (quantity = 0", "Quantité nulle", "lignes retirées : aucun besoin", "corrige"),
    ("DQ01", "dropped (negative", "Quantité négative", "lignes retirées", "corrige"),
    ("DQ01", "dropped (missing", "Quantité manquante", "lignes retirées", "corrige"),
    ("DQ02", "flagged and kept (outside", "Date hors de l'horizon", "lignes signalées et gardées", "info"),
    ("DQ02", "flagged and kept (no delivery on weekends", "Besoin un week-end", "gardé : livré un jour ouvré avant", "info"),
    ("DQ02", "flagged and kept (no delivery on", "Besoin un jour férié", "gardé : livré un jour ouvré avant", "info"),
    ("DQ03", "no cumulative date", "Date de cumul en fin de planning", "aucune", "info"),
    ("DQ03", "dropped (date", "Date de cumul en fin de planning", "lignes retirées : besoins cumulés après l'horizon", "corrige"),
    ("DQ04", "weight imputed (couple median", "Poids manquant ou nul", "complété par la médiane du couple fournisseur × usine", "corrige"),
    ("DQ04", "volume imputed (couple median", "Volume manquant ou nul", "complété par la médiane du couple fournisseur × usine", "corrige"),
    # runs launched with the former median (per piece of the material, then the supplier)
    ("DQ04", "weight imputed (material median", "Poids manquant ou nul", "complété par une médiane (matériel, sinon fournisseur)", "corrige"),
    ("DQ04", "volume imputed (material median", "Volume manquant ou nul", "complété par une médiane (matériel, sinon fournisseur)", "corrige"),
    ("DQ04", "weight imputed", "Poids manquant ou nul", "complété par un camion plein (24 500 kg)", "corrige"),
    ("DQ04", "volume imputed", "Volume manquant ou nul", "complété par un camion plein (110,4 m³)", "corrige"),
    ("DQ05", "dropped", "Ligne plus grande qu'un camion", "lignes retirées", "corrige"),
    ("DQ06", "kept first value", "Fournisseur en double dans les distances", "première distance gardée", "corrige"),
    ("DQ07", "median", "Fournisseur sans distance", "distance médiane des fournisseurs du planning", "corrige"),
    ("DQ07", "", "Fournisseur sans distance", "plus longue distance des fournisseurs du planning", "corrige"),
]


def anomaly(rule: str, action: str, rows: int) -> Anomaly:
    for code, start, label, treatment, kind in ANOMALIES:
        if code == rule and start in action:
            km = re.search(r"\(([\d.]+) km\)", action) if rule == "DQ07" else None
            return Anomaly(rule=rule, label=label, count=rows,
                           treatment=f"{treatment} ({float(km.group(1)):g} km)".replace(".", ",") if km else treatment, kind=kind)
    return Anomaly(rule=rule, label=action, count=rows, treatment="", kind="info")


def rules(manifest: RunManifest) -> list[str]:
    """Rules applied to the data and to the plan, with the parameters of the run."""
    p = parameters_of(manifest)
    advance = p.get("max_advance_days", 7)
    allowed = p.get("allowed_frequencies")
    frequencies = ", ".join(str(f) for f in allowed) if isinstance(allowed, list) else "1, 2, 3, 4, 5"
    budget = p.get("smoothing_budget", 0.0)
    smoothing = "seulement entre des plans de même coût" if not budget else f"avec au plus {float(str(budget)) * 100:g} % de surcoût"
    preparation = manifest.parameters.get("preparation")
    preparation = preparation if isinstance(preparation, dict) else {}
    load = ("complété par un camion plein : la ligne voyage seule" if preparation.get("missing_load", "full_truck") == "full_truck"
            else "complété par la médiane des lignes du même couple fournisseur × usine (à défaut, un camion plein)")
    distance = "la plus longue" if preparation.get("missing_distance", "max") == "max" else "la distance médiane"
    return [
        "Les lignes à quantité nulle, négative ou manquante sont retirées : elles ne portent aucun besoin.",
        "La dernière date du planning, qui cumule les besoins au-delà de l'horizon, est retirée.",
        f"Un poids ou un volume manquant est {load}.",
        f"Une ligne plus grande qu'un camion est retirée ; un fournisseur sans distance reçoit {distance} des distances du planning.",
        "Un camion ne transporte qu'un couple fournisseur × usine, et jamais plus de 24 500 kg ni 110,4 m³.",
        f"Aucune livraison en retard, et au plus {advance} jours calendaires d'avance ; le plan peut commencer {advance} jours "
        "avant le premier besoin.",
        f"Chaque couple livre sur un ensemble fixe de jours de la semaine ; profils autorisés : {frequencies} "
        "livraison(s) par semaine au plus.",
        "Aucune livraison le week-end ni un jour férié allemand : un jour de profil férié est livré le jour ouvré précédent.",
        "Chaque camion est payé selon la grille du transporteur (distance × poids), avec la marge et la surcharge diesel du PDF.",
        f"Les jours de livraison sont répartis entre les couples de chaque usine, {smoothing}.",
    ]


def bracket_labels(bounds: np.ndarray, unit: str) -> list[str]:
    values = [float(b) for b in bounds]
    return [f"> {thousands(values[i - 1])} {unit}" if np.isinf(b) else f"≤ {thousands(b)} {unit}" for i, b in enumerate(values)]


def rate_heatmap(trucks: pd.DataFrame, tariff: Tariff) -> Heatmap:
    """Trucks and their cost per cell of the rate sheet (distance bracket x weight bracket)."""
    row = np.searchsorted(tariff.km, trucks["distance"].to_numpy(float), side="left")
    col = np.searchsorted(tariff.kg, trucks["weight"].to_numpy(float), side="left")
    counts = np.zeros((len(tariff.km), len(tariff.kg)), dtype=int)
    costs = np.zeros((len(tariff.km), len(tariff.kg)))
    np.add.at(counts, (row, col), 1)
    np.add.at(costs, (row, col), trucks["cost"].to_numpy(float))
    return Heatmap(km=bracket_labels(tariff.km, "km"), kg=bracket_labels(tariff.kg, "kg"), trucks=counts.tolist(),
                   cost=np.round(costs, 2).tolist(), price=np.round(tariff.prices, 2).tolist())


def fill_histogram(trucks: pd.DataFrame) -> list[int]:
    """Trucks per fill bracket of 10 % (a full truck counts in the last bracket)."""
    brackets = np.clip((trucks["fill"].to_numpy(float) * 10).astype(int), 0, 9)
    return [int(n) for n in np.bincount(brackets, minlength=10)]


def situation(output_dir: Path, manifest: RunManifest, tariff: Tariff) -> Situation:
    """Current situation of the run's data: indicators, anomaly register, rules, calendar, trucks on the rate sheet (today and with
    the plan) and the fill of the trucks."""
    deliveries, trucks = table(output_dir, OutputName.CURRENT_DELIVERIES), table(output_dir, OutputName.CURRENT_TRUCKS)
    register, plan = table(output_dir, OutputName.ANOMALIES), table(output_dir, OutputName.TRUCKS)
    return Situation(
        kpi=SituationKpi(first=min(days_iso(deliveries, "date")), last=max(days_iso(deliveries, "date")),
                         lines=int(deliveries["lines"].sum()), deliveries=len(deliveries), trucks=len(trucks),
                         cost=float(trucks["cost"].sum()), fill_pct=100 * float(trucks["fill"].mean())),
        anomalies=[anomaly(rule, action, rows)
                   for rule, action, rows in zip(strs(register, "rule"), strs(register, "action"), ints(register, "rows"))],
        rules=rules(manifest),
        calendar=calendar(deliveries, "date", "trucks"),
        heatmap=rate_heatmap(trucks, tariff),
        heatmap_after=rate_heatmap(plan, tariff),
        fill_before=fill_histogram(trucks),
        fill_after=fill_histogram(plan),
    )


# --------------------------------------------------------------------------- trucks, profiles, suppliers
class TruckRow(BaseModel):
    id: str
    supplier: str
    plant: str
    date: str
    lines: int
    materials: int
    kg: float
    m3: float
    fill: float  # weight fill, %
    price: float
    replaces: int  # need days gathered in the truck


class TruckPage(BaseModel):
    total: int
    rows: list[TruckRow]


def trucks_page(output_dir: Path, plant: str | None, supplier: str | None, day: str | None, limit: int, offset: int) -> TruckPage:
    trucks, alloc = table(output_dir, OutputName.TRUCKS), table(output_dir, OutputName.ALLOCATIONS)
    per_truck = alloc.groupby("truck_id").agg(materials=("material", "nunique"), needs=("need_date", "nunique"))
    selected = trucks
    if plant:
        selected = selected[selected["plant"] == plant]
    if supplier:
        selected = selected[selected["supplier_id"] == supplier]
    if day:
        selected = selected[selected["date"].dt.date.astype(str) == day]
    page = selected.iloc[offset:offset + limit].join(per_truck, on="truck_id")
    rows = [TruckRow(id=i, supplier=s, plant=p, date=d, lines=n, materials=m, kg=kg, m3=m3, fill=100 * f, price=c, replaces=r)
            for i, s, p, d, n, m, kg, m3, f, c, r in zip(
                strs(page, "truck_id"), strs(page, "supplier_id"), strs(page, "plant"), days_iso(page, "date"), ints(page, "lines"),
                ints(page, "materials"), floats(page, "weight"), floats(page, "volume"), floats(page, "fill_weight"), floats(page, "cost"),
                ints(page, "needs"))]
    return TruckPage(total=len(selected), rows=rows)


class TruckItem(BaseModel):
    material: str
    unloading_point: str
    need_days: list[str]
    quantity: float
    kg: float
    m3: float
    imputed: bool


class TruckDetail(BaseModel):
    id: str
    supplier: str
    plant: str
    date: str
    kg: float
    m3: float
    price: float
    fill_weight: float
    fill_volume: float
    replaces: int
    items: list[TruckItem]


def join_days(days: pd.Series) -> str:
    """Need days of one material in a truck, sorted: "26/11, 27/11"."""
    return ", ".join(sorted({str(d) for d in days.tolist()}))


def truck_detail(output_dir: Path, truck_id: str) -> TruckDetail | None:
    trucks, alloc = table(output_dir, OutputName.TRUCKS), table(output_dir, OutputName.ALLOCATIONS)
    found = trucks[trucks["truck_id"] == truck_id]
    if found.empty:
        return None
    lines = alloc[alloc["truck_id"] == truck_id].assign(need_day=lambda f: f["need_date"].dt.strftime("%d/%m"))
    items = (lines.groupby(["material", "unloading_point"])
             .agg(need_days=("need_day", join_days), quantity=("schedule_quantity", "sum"),
                  kg=("weight", "sum"), m3=("volume", "sum"), imputed=("imputed_full_truck", "any"))
             .reset_index().sort_values("kg", ascending=False))
    return TruckDetail(
        id=truck_id, supplier=strs(found, "supplier_id")[0], plant=strs(found, "plant")[0], date=days_iso(found, "date")[0],
        kg=floats(found, "weight")[0], m3=floats(found, "volume")[0], price=floats(found, "cost")[0],
        fill_weight=100 * floats(found, "fill_weight")[0], fill_volume=100 * floats(found, "fill_volume")[0],
        replaces=int(lines["need_date"].nunique()),
        items=[TruckItem(material=m, unloading_point=u, need_days=d.split(", "), quantity=q, kg=kg, m3=m3, imputed=i)
               for m, u, d, q, kg, m3, i in zip(strs(items, "material"), strs(items, "unloading_point"), strs(items, "need_days"),
                                                floats(items, "quantity"), floats(items, "kg"), floats(items, "m3"), bools(items, "imputed"))],
    )


class ProfileRow(BaseModel):
    supplier: str
    plant: str
    km: float
    before: str
    after: str
    days: str
    days_before_smoothing: str
    frequency_changed: bool
    deliveries_before: int
    deliveries_after: int
    trucks: int
    fill: float  # mean weight fill, %


def profile_rows(output_dir: Path, plant: str | None, query: str | None) -> list[ProfileRow]:
    profiles = table(output_dir, OutputName.PROFILES)
    merged = (profiles.merge(table(output_dir, OutputName.SAVING)[["supplier_id", "plant", "deliveries_before", "deliveries_after", "trucks_after"]],
                             on=["supplier_id", "plant"])
              .merge(table(output_dir, OutputName.SMOOTHING_COUPLES)[["supplier_id", "plant", "profile_before_smoothing",
                                                                       "profile_days_before_smoothing"]], on=["supplier_id", "plant"]))
    if plant:
        merged = merged[merged["plant"] == plant]
    if query:
        merged = merged[merged["supplier_id"].str.contains(query.strip(), case=False, regex=False)]
    return [ProfileRow(supplier=s, plant=p, km=km, before=per_week(cur), after=per_week(new), days=days, days_before_smoothing=old_days,
                       frequency_changed=old != new, deliveries_before=db, deliveries_after=da, trucks=tr, fill=fill)
            for s, p, km, cur, new, days, old_days, old, db, da, tr, fill in zip(
                strs(merged, "supplier_id"), strs(merged, "plant"), floats(merged, "distance"), ints(merged, "current_profile"),
                ints(merged, "profile"), strs(merged, "profile_days"), strs(merged, "profile_days_before_smoothing"),
                ints(merged, "profile_before_smoothing"), ints(merged, "deliveries_before"), ints(merged, "deliveries_after"),
                ints(merged, "trucks_after"), floats(merged, "mean_fill_weight_pct"))]


class SupplierRow(BaseModel):
    supplier: str
    plant: str
    before: float
    after: float
    trucks_before: int
    trucks_after: int
    saving: float
    pct: float


def supplier_rows(output_dir: Path) -> list[SupplierRow]:
    saving = table(output_dir, OutputName.SAVING)
    return [SupplierRow(supplier=s, plant=p, before=b, after=a, trucks_before=tb, trucks_after=ta, saving=v, pct=pc)
            for s, p, b, a, tb, ta, v, pc in zip(strs(saving, "supplier_id"), strs(saving, "plant"), floats(saving, "cost_before"),
                                                 floats(saving, "cost_after"), ints(saving, "trucks_before"), ints(saving, "trucks_after"),
                                                 floats(saving, "saving"), floats(saving, "saving_pct"))]

