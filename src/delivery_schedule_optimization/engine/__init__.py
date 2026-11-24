"""Delivery planner (Milestone 3): a fixed set of delivery days per supplier x plant couple.

Adapted from `fast_plan.py` on the `prime` branch (line-level truck packing, 7-day window,
per-truck pricing, automatic checks) with these corrections:

1. Scenario B (supplier x plant) is the official scenario. The hub variant, where one truck
   serves several plants, breaks RG01 and is not produced.
2. Profile (RG05, business answer): every couple delivers on a FIXED SET of weekdays. Each week it
   may use any part of this set (Mon+Tue+Wed one week, Tue+Wed the next, Mon+Wed the next), never
   another day. The profile is the largest number of deliveries made in one week over the period,
   and it must equal the number of days in the set ("3 2 1 1 1" = profile 3 on 3 fixed days).
3. Every line of the consolidated dataset is planned, imputed loads included (RG08).
4. Delivery days are spread between the couples of each plant (RG09), without changing the
   profile chosen by RG07.
5. Nothing ships before the schedule is released (start of the plan).

Optimisation (exact):
- per couple and set of days, the departure days are chosen by dynamic programming. With RG06 (a delivery
  day covers the needs up to the next delivery day, excluded) a plan is defined by its departure days, and
  its cost is a sum over consecutive departures: the best plan is a shortest path, as in dynamic lot
  sizing with demand time windows (Lee, Cetinkaya & Wagelmans, Management Science 2001);
- smoothing (RG09) chooses one plan per couple for all the couples of a plant together, as an integer
  linear programme solved to optimality (HiGHS), like the choice of visit-day combinations in periodic
  routing or of delivery patterns in retail (Sternbeck & Kuhn 2014).

Choice of the profile (RG07 + client answer "add delivery days rather than trucks"): the lowest
profile whose departures each fit in one truck; a truck holding a single line completed with a full
truck (unknown weight) is not counted as an overflow, since no profile can avoid it. If even 5 days
overflow, the profile with the fewest overflowing departures is kept and trucks are added.
"""
