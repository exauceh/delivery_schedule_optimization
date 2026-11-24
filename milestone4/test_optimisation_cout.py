from bisect import bisect_left
from datetime import date
from itertools import product
import unittest

import pandas as pd

from delivery_schedule_optimization.fast_plan import Line
from delivery_schedule_optimization.lissage_publie import Plan as PublishedPlan, select_plans, strict_profile
from delivery_schedule_optimization.optimisation_cout import PROFILES, combine_profiles, solve_pair, solve_profile_partition
from delivery_schedule_optimization.partition_hall import Departure, Need, allocate_witness, solve_profile


def brute(needs, slots, witness, bands, prices):
    """Independent exhaustive oracle: partitions first, then every possible dating."""
    def partitions(i, groups):
        if i == len(needs):
            yield groups
            return
        yield from partitions(i + 1, groups + [(i,)])
        for k, group in enumerate(groups):
            yield from partitions(i + 1, groups[:k] + [(*group, i)] + groups[k+1:])

    best = None
    for groups in partitions(0, []):
        weights = [sum(needs[i].grams for i in g) for g in groups]
        volumes = [sum(needs[i].millilitres for i in g) for g in groups]
        if max(weights) > 24_500_000 or max(volumes) > 110_400_000:
            continue
        dates = [[slot for slot in slots if all(0 <= (needs[i].due - slot.actual).days <= 7 for i in group)]
                 for group in groups]
        if any(set(witness) <= set(used) for used in product(*dates)):
            cost = sum(prices[bisect_left(bands, kg)] for kg in weights)
            best = cost if best is None else min(best, cost)
    return best


class TestPartition(unittest.TestCase):
    def compare(self, needs, profile, earliest, slots, witness, expected, *, holidays=frozenset(),
                bands=(24_500_000,), prices=(100,)):
        self.assertEqual(brute(needs, slots, witness, bands, prices), expected)
        for dated in (False, True):
            plan = solve_profile(needs, profile, earliest, bands, prices, dated=dated, holidays=holidays)
            self.assertEqual(None if plan is None else plan.cost, expected)
        return plan

    def test_same_truck_cannot_cover_two_days(self):
        mon, thu = date(2026, 11, 9), date(2026, 11, 12)
        slots = (Departure(mon, mon), Departure(thu, thu))
        self.compare([Need("a", thu, 10_000, 500)], (0, 3), mon, slots, slots, None)

    def test_hall_catches_nonconsecutive_subset(self):
        slots = tuple(Departure(d, d) for d in (date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11)))
        self.assertIsNone(allocate_witness([slots, (slots[1],), (slots[1],)], slots))

    def test_holiday_shift_nominal_days_can_share_effective_day_not_truck(self):
        thu, fri = date(2026, 4, 30), date(2026, 5, 1)
        slots = (Departure(thu, thu), Departure(fri, thu))
        plan = self.compare([Need("a", thu, 50_000, 500), Need("b", thu, 50_000, 500)],
                            (3, 4), thu, slots, slots, 200, holidays={fri})
        self.assertEqual({t.departure.actual for t in plan.trucks}, {thu})
        self.assertEqual({t.departure.nominal for t in plan.trucks}, {thu, fri})

    def test_seven_day_boundary_when_pre_9_opens(self):
        fri, mon, next_fri = date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 13)
        needs = [Need("a", mon, 50_000, 500), Need("b", next_fri, 50_000, 500)]
        a, b = Departure(fri, fri), Departure(next_fri, next_fri)
        self.compare(needs, (4,), mon, [b], [b], None)
        self.compare(needs, (4,), fri, [a, b], [a], 100)

    def test_tariff_can_make_two_trucks_cheaper(self):
        mon = date(2026, 11, 9)
        slot = Departure(mon, mon)
        self.compare([Need("a", mon, 50_000, 500), Need("b", mon, 1_000_000, 500)], (0,), mon,
                     [slot], [slot], 4927, bands=(50_000, 1_000_000, 24_500_000), prices=(826, 4101, 5120))

    def test_volume_blocks_a_light_merge(self):
        mon = date(2026, 11, 9)
        slot = Departure(mon, mon)
        self.compare([Need("a", mon, 100_000, 110_400_000), Need("b", mon, 100_000, 1)],
                     (0,), mon, [slot], [slot], 200)

    def test_strengthened_relaxation_preserves_exact_loading_bound(self):
        monday = date(2026, 11, 9)
        bands = (50, 1000, 4000, 24500)
        prices = (100, 200, 300, 400)
        for loads, expected in [
            ([(0.5, 60), (0.5, 60), (0.5, 1)], 200),
            ([(1500, 60), (1500, 60), (1200, 1)], 600),
            ([(40, 3), (40, 5)], 200),
            ([(50, 1)], 100),
        ]:
            with self.subTest(loads=loads):
                lines = [Line(str(i), "S", "P", "Q", str(i), monday, 1, kg, m3, 10)
                         for i, (kg, m3) in enumerate(loads)]
                data = ("synthetic", lines, (monday,), bands, prices, ())
                exact, _ = solve_pair((*data, 8, 0, False, monday))
                relaxed, _ = solve_pair((*data, 0, 0, False, monday, False, False, True))
                self.assertEqual(exact["upper"], expected)
                self.assertLessEqual(relaxed["lower"], exact["upper"])
                self.assertEqual(relaxed["lower"], expected)

    def test_compact_tariff_patterns_match_full_enumeration(self):
        monday = date(2026, 11, 9)
        friday = date(2026, 11, 13)
        bands = (50, 1000, 24500)
        prices = (826, 4101, 5120)
        for workdays, loads, expected in [
            ((monday,), [(monday, 50, 1), (monday, 1000, 1)], 4927),
            ((monday,), [(monday, 25, 1), (monday, 25, 1)], 826),
            ((monday,), [(monday, 25, 60), (monday, 25, 60)], 1652),
            ((monday,), [(monday, 25, 110.4), (monday, 25, 1)], 1652),
            ((monday,), [(monday, 24500, 1), (monday, 25, 1)], 5946),
            ((monday,), [(monday, 50, 1), (monday, .001, 1)], 1652),
            ((monday, friday), [(monday, 40, 1), (friday, 10, 1), (friday, 1000, 1)], 4927),
        ]:
            with self.subTest(loads=loads):
                lines = [Line(str(i), "S", "P", "Q", str(i), due, 1, kg, m3, 10)
                         for i, (due, kg, m3) in enumerate(loads)]
                data = ("synthetic", lines, workdays, bands, prices, ())
                full, _ = solve_pair((*data, 8, 0, False, monday))
                compact, _ = solve_pair((*data, 0, 0, False, monday, True, True, True))
                self.assertEqual(full["upper"], expected)
                self.assertEqual((compact["lower"], compact["upper"]), (expected, expected))

    def test_strict_cutoff_and_profile_partition_prove_the_cost(self):
        monday = date(2026, 11, 9)
        friday = date(2026, 11, 13)
        lines = [Line("a", "S", "P", "D", "A", monday, 1, 25, 1, 10),
                 Line("b", "S", "P", "D", "B", friday, 1, 25, 1, 10)]
        bands, prices = (50, 1000, 24500), (826, 4101, 5120)
        seed = ("incumbent", (0,), [(monday, ("a", "b"))])
        job = ("synthetic", lines, (monday, friday), bands, prices, [seed],
               0, 0, False, monday, True, True, True, True, 0)
        strict, _ = solve_pair(job)
        partition, _ = solve_profile_partition((*job, 826, False))
        fractional, _ = solve_profile_partition((*job, 826, True, True))
        self.assertEqual(strict["solver_status"], 2)
        self.assertEqual((partition["lower"], partition["upper"]), (826, 826))
        self.assertEqual(partition["open_profiles"], "")
        self.assertEqual((fractional["lower"], fractional["upper"]), (826, 826))

    def test_profile_partition_improves_a_valid_expensive_seed(self):
        monday = date(2026, 11, 9)
        friday = date(2026, 11, 13)
        lines = [Line("a", "S", "P", "D", "A", monday, 1, 25, 1, 10),
                 Line("b", "S", "P", "D", "B", friday, 1, 25, 1, 10)]
        seed = ("incumbent", (0, 4), [(monday, ("a",)), (friday, ("b",))])
        job = ("synthetic", lines, (monday, friday), (50, 1000, 24500), (826, 4101, 5120), [seed],
               0, 0, False, monday, True, True, True, True, 0)
        result, trucks = solve_profile_partition((*job, 1652, False))
        self.assertEqual((result["lower"], result["upper"]), (826, 826))
        self.assertEqual(len(trucks), 1)

    def test_profile_aggregation_keeps_an_unresolved_branch_in_the_bound(self):
        branches = {p: ({"lower": 100, "upper": None}, []) for p in reversed(PROFILES)}
        branches[(0,)] = ({"lower": 100, "upper": 100, "trucks": 1}, [("truck",)])
        branches[(1, 4)] = ({"lower": 90, "upper": None}, [])
        stats, _ = combine_profiles(branches)
        self.assertEqual((stats["lower"], stats["upper"], stats["open_profiles"]), (90, 100, "14"))
        self.assertFalse(stats["certified"])
        del branches[(1, 4)]
        with self.assertRaisesRegex(ValueError, "Some weekday profiles"):
            combine_profiles(branches)


class TestPublishedSmoothing(unittest.TestCase):
    def test_strict_profile_requires_all_departures_and_one_witness_week(self):
        monday = "2026-11-09"
        tuesday = "2026-11-10"
        self.assertTrue(strict_profile(pd.Series(pd.to_datetime([monday, tuesday])), (0, 1)))
        self.assertFalse(strict_profile(pd.Series(pd.to_datetime([monday, tuesday])), (0,)))
        self.assertFalse(strict_profile(pd.Series(pd.to_datetime([monday, "2026-11-17"])), (0, 1)))

    def test_selection_preserves_cost_priority_before_global_peak(self):
        def plan(name, date, cents):
            return PublishedPlan(name, (0,), pd.DataFrame(), pd.DataFrame({"date": [date], "cents": [cents]}))

        selected = select_plans({
            ("A", "P"): [plan("same-day", "2026-11-09", 10), plan("shifted", "2026-11-10", 12)],
            ("B", "P"): [plan("cheap", "2026-11-09", 11), plan("expensive", "2026-11-11", 17)],
            ("C", "Q"): [plan("overlap", "2026-11-09", 13), plan("separate", "2026-11-11", 13)],
        }, {"P": 1, "Q": 1})
        self.assertEqual([p.source for p in selected.values()], ["shifted", "cheap", "separate"])
        self.assertEqual(sum(p.cost for p in selected.values()), 36)


if __name__ == "__main__":
    unittest.main()
