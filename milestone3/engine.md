# How the optimisation engine works

This document explains the delivery consolidation engine of milestone 3. For every step, it links the mathematical model written in the first cell of [milestone3.ipynb](milestone3.ipynb) to the code that implements it in [engine.py](../src/delivery_schedule_optimization/engine.py).

Line numbers refer to the current version of `engine.py`.

---

## 1. The pipeline at a glance

```
needs (consolidated_dataset.csv) + rate sheet + calendar
        │
        ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ FIRST PASS: one MILP per couple × profile N (900 problems)  │  solve_couple, solve_all
 │ chooses the allowed weekdays and the delivery day of every  │
 │ line, minimising the transport cost                         │
 └─────────────────────────────────────────────────────────────┘
        │  best plan per couple and per N        best_per_profile
        ▼
   profile choice: smallest N at the best cost   notebook, section 5
        │
        ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ SECOND PASS: smoothing per plant (RG09)                     │  build_options, smooth_plant
 │ prices every weekday set of each couple, then picks one set │
 │ per couple to lower the daily truck peak of each plant      │
 └─────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ LOADING: whole lines into trucks, delivery by delivery      │  load_delivery, _pack
 │ fewest trucks first, then cheapest; each truck priced alone │
 └─────────────────────────────────────────────────────────────┘
        │
        ▼
   rule checks, comparison with the current situation, exports   notebook, sections 6 and 7
```

The notebook holds the data preparation, the business choices (profile rule, checks, charts, exports) and the parameters. The engine module holds the optimisation models. The engine must be a separate, importable module because the parallel workers (`ProcessPoolExecutor`) import it in every process.

---

## 2. Data structures

| Class | Line | Role |
| :--- | :--- | :--- |
| `SolverSettings` | [L30](../src/delivery_schedule_optimization/engine.py#L30) | Parameters shared by every solve: truck capacity ($Q$, $\bar V$), anticipation window, $\varepsilon$, time limit, MIP gap. |
| `CoupleInstance` | [L42](../src/delivery_schedule_optimization/engine.py#L42) | Data of one couple $c = (f, u)$: its lines $L_c$ with need day $i_l$, weight $W_l$ and volume $V_l$, plus the price $p_{c,k}$ and the upper bound $\bar w_k$ of every weight bracket. |
| `CoupleSolution` | [L61](../src/delivery_schedule_optimization/engine.py#L61) | Plan of one couple for one profile: delivery day of every line, cost, solver status, weekdays used. |
| `DeliveryLoad` | [L192](../src/delivery_schedule_optimization/engine.py#L192) | Result of loading one delivery: truck number of every line, weight, volume and cost of each truck. |
| `CoupleOption` | [L349](../src/delivery_schedule_optimization/engine.py#L349) | One weekday set of a couple, with its plan, its loaded cost and its trucks per day. This is the input of the smoothing. |
| `PlantSmoothing` | [L360](../src/delivery_schedule_optimization/engine.py#L360) | Result of the smoothing of one plant: chosen option per couple, peak and cost before and after. |

The notebook builds one `CoupleInstance` per couple in `build_instances` (section 2). The price of a couple in bracket $k$ is the base price of the rate sheet for its distance row, multiplied by $(1 + \text{margin}) \times \text{diesel}$.

---

## 3. First pass: one MILP per couple and per profile

`solve_couple` ([L77](../src/delivery_schedule_optimization/engine.py#L77)) builds and solves the model of the first notebook cell for **one** couple $c$ and **one** profile $N$ (`n_max`).

### 3.1 Why one problem per couple

No constraint of the first pass links two couples: two suppliers never share a truck (RG01), and the cost of a couple only depends on its own lines. The global problem is therefore the sum of 180 independent problems, and solving each one separately gives exactly the same optimum as one huge model, much faster.

### 3.2 Sets

| Math | Code | Line |
| :--- | :--- | :--- |
| $J$, delivery calendar (working days, no German holiday) | `calendar` → `days` (built in the notebook, section 1) | [L96](../src/delivery_schedule_optimization/engine.py#L96) |
| $A_l = \{ j \in J : i_l - 7 \le j \le i_l \}$, possible delivery days of line $l$ | `days[first[l]:last[l]]`, found with `np.searchsorted` | [L98-L99](../src/delivery_schedule_optimization/engine.py#L98-L99) |
| $wd(j)$, weekday of day $j$ | `weekday(days)` (0 = Monday) | [L72](../src/delivery_schedule_optimization/engine.py#L72) |
| $K$, weight brackets | `brackets = range(len(inst.bracket_upper))` | [L115](../src/delivery_schedule_optimization/engine.py#L115) |

Because $A_l$ is computed first, a variable $z_{c,l,j}$ is only created for the days where line $l$ may arrive. The anticipation window (RG03) is therefore enforced by construction: there is no variable for a forbidden day.

### 3.3 Variables

| Math | Meaning | Code | Line |
| :--- | :--- | :--- | :--- |
| $z_{c,l,j} \in \{0,1\}$ | line $l$ is delivered on day $j$ | `z_{li}_{dj}`, stored in `by_line` (per line) and `by_day` (per day) | [L107-L113](../src/delivery_schedule_optimization/engine.py#L107-L113) |
| $x_{c,j} \in \{0,1\}$ | the couple is delivered on day $j$ | `x[dj]` | [L116](../src/delivery_schedule_optimization/engine.py#L116) |
| $F_{c,j} \in \mathbb{N}$ | full trucks on day $j$, at the flat price | `full[dj]` | [L117](../src/delivery_schedule_optimization/engine.py#L117) |
| $y_{c,j,k} \in \{0,1\}$ | the last truck of day $j$ is billed in bracket $k$ | `y[dj, k]` | [L118](../src/delivery_schedule_optimization/engine.py#L118) |
| $s_{c,d} \in \{0,1\}$ | weekday $d$ is an allowed day of the couple | `s[d]` | [L119](../src/delivery_schedule_optimization/engine.py#L119) |
| $h_{c,w} \in \{0,1\}$ | week $w$ delivers every allowed weekday | `h[wk]` | [L156](../src/delivery_schedule_optimization/engine.py#L156) |

The variables are only created for the days that appear in `by_day`, that is the days where at least one line may arrive. This keeps every model small.

### 3.4 Objective

$$\min \; \sum_{j} \Big( p^{\max}_c \, F_{c,j} + \sum_{k} p_{c,k} \, y_{c,j,k} + \varepsilon \, x_{c,j} \Big)$$

| Term | Code | Line |
| :--- | :--- | :--- |
| $p^{\max}_c F_{c,j}$: full trucks at the flat price (last column of the rate sheet, `prices[-1]`) | `inst.prices[-1] * full[dj]` | [L121](../src/delivery_schedule_optimization/engine.py#L121) |
| $p_{c,k} \, y_{c,j,k}$: the last truck at the price of its bracket | `inst.prices[k] * y[dj, k]` | [L121-L123](../src/delivery_schedule_optimization/engine.py#L121-L123) |
| $\varepsilon \, x_{c,j}$: 1 cent per delivery day, a tie-break towards fewer deliveries | `settings.delivery_penalty_eur * lpSum(x)` | [L124](../src/delivery_schedule_optimization/engine.py#L124) |

The expression `transport` (without $\varepsilon$) is the cost reported as `model_cost`.

### 3.5 Constraints

| # | Math | Meaning | Code | Line |
| :--- | :--- | :--- | :--- | :--- |
| (1) | $\sum_{j \in A_l} z_{c,l,j} = 1$ | every line is delivered once, inside its window (RG03, RG08) | loop on `by_line` | [L126-L127](../src/delivery_schedule_optimization/engine.py#L126-L127) |
| (2) | $z_{c,l,j} \le x_{c,j}$ | no line on a day without delivery | loop on `by_day` | [L129-L130](../src/delivery_schedule_optimization/engine.py#L129-L130) |
| (3) | $\sum_l W_l z_{c,l,j} \le Q F_{c,j} + \sum_k \bar w_k y_{c,j,k}$ | the weight of the day fits in the paid trucks (RG04, RG11) | `settings.truck_kg * full[dj] + ...` | [L131-L133](../src/delivery_schedule_optimization/engine.py#L131-L133) |
| (4) | $\sum_l V_l z_{c,l,j} \le \bar V (F_{c,j} + \sum_k y_{c,j,k})$ | the volume of the day fits in the trucks (RG04) | `settings.truck_m3 * (...)` | [L134-L136](../src/delivery_schedule_optimization/engine.py#L134-L136) |
| (5) | $\sum_k y_{c,j,k} = x_{c,j}$ | a delivery has exactly one "last truck" | | [L137](../src/delivery_schedule_optimization/engine.py#L137) |
| (6) | $F_{c,j} \le M_{c,j} x_{c,j}$ and $x_{c,j} \le \sum_l z_{c,l,j}$ | no truck without delivery, no empty delivery ($M_{c,j}$ = number of possible lines that day) | | [L138-L139](../src/delivery_schedule_optimization/engine.py#L138-L139) |
| (7) | $x_{c,j} \le s_{c,wd(j)}$ and $\sum_d s_{c,d} \le N$ | deliveries only on allowed weekdays, at most $N$ of them (RG05) | | [L140-L141](../src/delivery_schedule_optimization/engine.py#L140-L141) |
| (8) | $x_{c,j} \ge s_{c,wd(j)} + h_{c,w(j)} - 1$ and $\sum_w h_{c,w} \ge 1$ | the profile is reached: at least one week delivers every allowed weekday | `if profile_reached:` block | [L146-L164](../src/delivery_schedule_optimization/engine.py#L146-L164) |

Notes on the code of each constraint:

- **(3) and (5) together are the cost function.** The solver pays $F$ full trucks at the flat price, then one last truck in a bracket $k$ whose upper bound $\bar w_k$ covers the rest of the weight. Since the price grows with the bracket, the solver always picks the cheapest bracket that covers the rest. No lower bound per bracket is needed.
- **(3) and (4) check the total of the day, as if a line could be split between two trucks.** This is an approximation that keeps the model fast. The real loading, with whole lines, is done afterwards (section 5), and the published cost comes from it.
- **(8) in the code.** The days are grouped by Monday-based week number: `(day_number + 3) // 7` works because 1970-01-01 was a Thursday. Only the whole weeks of the horizon can be the reference week ([L151-L152](../src/delivery_schedule_optimization/engine.py#L151-L152)). For a day $j$ in week $w$, if $s_{wd(j)} = 1$ and $h_w = 1$, then $x_j$ must be 1. If no line can arrive on that day (no `x[dj]`), the week cannot be the reference week for that weekday ([L163](../src/delivery_schedule_optimization/engine.py#L163)). A holiday is not in the calendar, so it is simply skipped.
- **Why (8):** without it, a couple delivered once a week could keep 5 allowed weekdays and pick the cheapest one each week. It would show a profile of 5 and make the plant reserve slots that are never used.
- **Validation.** With `window_days=0` and `profile_reached=False`, every line can only arrive on its need day. The notebook checks that the engine then reproduces exactly the current deliveries of milestone 2 (section 3).
- **Imposed weekdays.** The optional argument `weekdays` fixes every $s_{c,d}$ to 0 or 1 ([L142-L144](../src/delivery_schedule_optimization/engine.py#L142-L144)). It is only used by the smoothing (section 6).

### 3.6 Solve and read the result

- **Solve.** HiGHS is called through PuLP with a time limit (`time_limit_s`, 5 s), a relative gap (`mip_gap`, 1 %) and one thread, because the parallelism is handled one level up ([L167](../src/delivery_schedule_optimization/engine.py#L167)).
- **Read the result.** For each line, the code takes the day whose $z$ equals 1 ([L170-L175](../src/delivery_schedule_optimization/engine.py#L170-L175)). If a line has none, no feasible plan was found in time, and a `RuntimeError` is raised.
- **The status** is kept in `CoupleSolution.status`: "Optimal Solution Found" means that the gap is at most 1 %, and "Solution Found" means that the time limit stopped the solver with a good plan.

**How the solver goes fast (branch and bound).** HiGHS first solves the linear relaxation, where every binary variable may take fractional values. This gives a lower bound on the cost. It then fixes fractional variables one by one: for example "line 12 on Monday" is set to 1 in one branch and to 0 in the other. It discards every branch whose lower bound is already above the best plan found. It stops when the best plan is within `mip_gap` of the lower bound.

The formulation with $F$ (full trucks) plus one last-truck bracket $y$ was chosen because its relaxation is close to the real cost. This is why the solver discards many branches early. A first version, with one binary per truck and per bracket, took 36 minutes.

---

## 4. Running the 900 problems and choosing the profile

### 4.1 Parallel solve

`solve_all` ([L322](../src/delivery_schedule_optimization/engine.py#L322)) creates one task per couple and per profile $N \in \{1, \dots, 5\}$, that is 180 × 5 = 900 tasks. It sends them to a `ProcessPoolExecutor` with one process per CPU core (`workers`).

Each task runs `_solve_task` ([L310](../src/delivery_schedule_optimization/engine.py#L310)): if the solver finds no plan at all within the time limit (this can happen to a large couple at $N = 1$ when the machine is busy), the problem is solved again once with 6 times the limit (`RETRY_TIME_FACTOR`).

The tasks are sorted from the largest to the smallest ([L332](../src/delivery_schedule_optimization/engine.py#L332)). The long problems start first, and the short ones fill the gaps at the end, which shortens the total time.

### 4.2 Best plan per profile

`best_per_profile` ([L291](../src/delivery_schedule_optimization/engine.py#L291)) uses a simple property: a plan that uses $m$ weekdays is also valid for every profile $N \ge m$. For each $N$, it keeps the cheapest plan among all the plans solved for the couple that use at most $N$ weekdays.

This has two effects:
- The cost can never increase with $N$, even when the solver stopped on its time limit for one $N$ and found a better plan for another.
- Many plans are "taken from another N" (`seconds = 0`): the plan solved for $N = 3$ may use only 2 weekdays and then also serves $N = 2$ if it is cheaper.

### 4.3 Profile rule (notebook, section 5)

For each couple, the notebook keeps the **smallest $N$ whose cost is within `profile_tolerance` of the best cost of the couple** (0 by default, so the smallest $N$ that reaches the best cost). With constraint (8), the number of allowed weekdays of the chosen plan is also its highest weekly frequency.

This rule (RG07) is a business choice. The cost of every $N$ is exported (`cost_N1` to `cost_N5` in `optimized_couples.csv`), so the rule can change without running the solver again.

---

## 5. Loading: whole lines into trucks

### 5.1 From the optimisation output to the loading

The optimisation decides **on which day** each line is delivered. The loading then decides **in which truck** it travels.

**What the optimisation returns.** For each couple, the optimisation (first pass, then smoothing) returns a plan, a `CoupleSolution`:

| Field | Content | Example: SUP_00085 × PLT_004 |
| :--- | :--- | :--- |
| `delivery_days` | the **delivery day of every line**, in the order of `line_ids` | line MAT_000298 needed on 19/11 → Monday 16/11 |
| `weekdays` | the allowed weekdays, hence the profile | `(0,)` = Monday, profile 1 |
| `model_cost` | the cost estimated by the model, with capacity checked on the total of the day | only used to choose the profile |

The plan does **not** say which line goes into which truck. It only guarantees that, every day, the total load fits in the trucks it counts. In the model, a "full truck" ($F_{c,j}$) is a truck billed at the flat price and assumed to carry a whole capacity (24.5 t or 110.4 m³), as if a line could be split. Only the last truck of a delivery is partly filled.

**How the plan reaches the loading** (notebook, section 6):

1. `delivery_dates(plans)` puts the plans of all the couples end to end. The result gives the delivery day of each of the 4,389 lines.
2. `plan = needs.assign(delivery_date=...)` adds this column to the needs. Every line keeps its material, unloading point, weight and volume.
3. `load_plan(plan)` groups the lines by **couple × delivery day**, which is one delivery. For each delivery, it calls `load_delivery` with the weights and volumes of its lines.

**What the loading does for one delivery:**

- **If everything fits in one truck** (≤ 24.5 t and ≤ 110.4 m³): one truck, no computation. This is the case for about 600 of the 654 deliveries.
- **Otherwise**, a small MILP puts the whole lines into the fewest trucks, then at the lowest cost (section 5.2).
- **In both cases**, each truck is priced on its own weight.

It returns the **truck number of every line**, and the weight, volume and cost of every truck.

Because lines are never split, a delivery may have several trucks that are not full. For example, SUP_00046 × PLT_004 on 25/11 (58.3 t) needs 3 trucks of 24.4 t, 18.6 t and 15.2 t: its 15.2 t line fits with neither of the other two trucks. This is rare (2 of the 53 multi-truck deliveries), because most lines are small compared with a truck.

**Example: SUP_00085 × PLT_004, Monday 16/11**

```
Optimisation : 31 lines (needs from 16/11 to 20/11) → delivered on Monday 16/11
Loading      : 9.7 t and 28 m³ → everything fits in one truck → truck no. 1, 331 €
```

**What is built from it:**

| Result | Built from | File |
| :--- | :--- | :--- |
| Line → day + truck | `delivery_date` (optimisation) + `truck_no` (loading) | `optimized_lines.csv` |
| One truck: weight, volume, fill rate, cost | loading | `optimized_trucks.csv` |
| Materials in each truck (Output 2) | lines grouped by truck | `optimized_truck_materials.csv` |
| One delivery: trucks and cost | sum over its trucks | `optimized_deliveries.csv` |
| Total cost and saving | sum of the truck costs | `optimized_kpis.csv` |

**The published cost always comes from the loading, never from the model.** The model chooses the days; the loading gives the real trucks and the real price.

### 5.2 The loading model

The first pass checks capacity on the total of each day. A line is never split, because its volume is not divisible. `load_delivery` ([L260](../src/delivery_schedule_optimization/engine.py#L260)) therefore loads the whole lines of one delivery (one couple × one day) into trucks, and prices each truck on its own weight.

| Step | Code | Line |
| :--- | :--- | :--- |
| A line larger than a truck is an error | `ValueError` | [L272](../src/delivery_schedule_optimization/engine.py#L272) |
| Everything fits in one truck: one truck, no MILP (most deliveries) | `if weights.sum() <= ...` | [L274](../src/delivery_schedule_optimization/engine.py#L274) |
| Otherwise, try a growing number of truck slots, starting from the capacity lower bound | `lower`, `for slots in ...` | [L283-L287](../src/delivery_schedule_optimization/engine.py#L283-L287) |
| Loading MILP for a given number of slots | `_pack` | [L214](../src/delivery_schedule_optimization/engine.py#L214) |

The loading MILP in `_pack`:

| Math | Code | Line |
| :--- | :--- | :--- |
| $a_{l,b} \in \{0,1\}$: line $l$ in truck $b$; $t_b$: truck $b$ is used; $y_{b,k}$: bracket of truck $b$ | `a`, `used`, `y` | [L225-L227](../src/delivery_schedule_optimization/engine.py#L225-L227) |
| $\min \; P \sum_b t_b + \sum_{b,k} p_k y_{b,k}$, with $P$ larger than any price difference | `truck_penalty * lpSum(used) + ...` | [L229-L230](../src/delivery_schedule_optimization/engine.py#L229-L230) |
| $\sum_b a_{l,b} = 1$: a line in exactly one truck | | [L232](../src/delivery_schedule_optimization/engine.py#L232) |
| $\sum_k y_{b,k} = t_b$: a used truck has one bracket | | [L234](../src/delivery_schedule_optimization/engine.py#L234) |
| $\sum_l W_l a_{l,b} \le \sum_k \bar w_k y_{b,k}$: weight within the bracket | | [L235-L237](../src/delivery_schedule_optimization/engine.py#L235-L237) |
| $\sum_l V_l a_{l,b} \le \bar V t_b$: volume | | [L238](../src/delivery_schedule_optimization/engine.py#L238) |
| $t_b \le t_{b-1}$: trucks used in order (removes identical, permuted solutions) | | [L239-L240](../src/delivery_schedule_optimization/engine.py#L239-L240) |

**Why the number of trucks comes first:** the rate sheet is not subadditive. For example, 1,050 kg in one truck costs more than two trucks of 1,000 kg and 50 kg. A pure cost minimum would send extra trucks only to land in cheaper brackets, which makes no sense on the ground. The big coefficient $P$ makes one truck more expensive than any price difference. The solver therefore first minimises the number of trucks, then the cost.

After the solve, the trucks are renumbered so that truck 1 is the heaviest ([L246-L248](../src/delivery_schedule_optimization/engine.py#L246-L248)), and each truck is priced with `truck_cost` ([L206](../src/delivery_schedule_optimization/engine.py#L206)): the smallest bracket whose inclusive upper bound covers its weight.

In the notebook, `load_plan` (section 2.2) calls `load_delivery` for every delivery of a plan. This loaded cost is the one published, for the current situation as well as for the optimised plan. The milestone 2 estimator, which treated the load as divisible, is kept only to measure its bias (+0.14 %).

---

## 6. Second pass: smoothing per plant (RG09)

### 6.1 The problem

The first pass chooses the weekdays of each couple without looking at the other couples of the same plant. Many couples have several weekdays at the same cost, and the solver picks one arbitrarily, often the same one. Before smoothing, PLT_002 received 57 trucks on Friday 20/11 and 2 to 4 trucks on the other days of that week.

The smoothing is the only rule that links the couples of a plant. Putting it inside the first-pass model would mean one huge problem per plant (up to 64 couples and 1,500 lines together). It would lose the parallelism, and its relaxation would be poor. It is therefore done as a second pass, which keeps the profile $N_c$ of every couple and only changes its weekdays.

### 6.2 Step 1: price every weekday set (`build_options`)

`build_options` ([L397](../src/delivery_schedule_optimization/engine.py#L397)) builds the set $O_c$ of every couple:
- **option 0** is the plan of the first pass $o^1_c$, loaded with `evaluate_plan` ([L372](../src/delivery_schedule_optimization/engine.py#L372));
- **every other combination of $N_c$ weekdays** (5 options for $N_c = 1$, 10 for $N_c = 2$ or 3) is solved with `solve_couple(..., weekdays=combo)`. The first-pass model is used with the $s_{c,d}$ imposed, then the plan is loaded truck by truck (`_solve_option`, [L385](../src/delivery_schedule_optimization/engine.py#L385)).

For every option, the code keeps its loaded cost $\kappa_{c,o}$ (`CoupleOption.cost`) and its number of trucks on each day $\tau_{c,o,j}$ (`CoupleOption.days`, `CoupleOption.trucks`). About 1,000 small solves run in parallel, largest first, in about 40 s. A weekday set with no feasible plan (for example one that cannot satisfy constraint (8)) is skipped.

### 6.3 Step 2: choose one weekday set per couple (`smooth_plant`)

`smooth_plant` ([L431](../src/delivery_schedule_optimization/engine.py#L431)) solves, for one plant $u$, a small MILP in two stages:

| Math | Code | Line |
| :--- | :--- | :--- |
| $u_{c,o} \in \{0,1\}$: couple $c$ gets weekday set $o$ | `u[c, o]` | inside `solve` |
| $P_u \ge 0$: trucks received on the busiest day | `peak` | inside `solve` |
| $\sum_o u_{c,o} = 1$: one weekday set per couple | | [L462](../src/delivery_schedule_optimization/engine.py#L462) |
| $\sum_c \sum_o \tau_{c,o,j} u_{c,o} \le P_u$ for every day $j$ | `load[day]` | [L464-L469](../src/delivery_schedule_optimization/engine.py#L464-L469) |
| $\sum_c \sum_o \kappa_{c,o} u_{c,o} \le (1 + \text{tol}) \sum_c \kappa_{c,o^1_c}$: cost budget | `budget` | [L445](../src/delivery_schedule_optimization/engine.py#L445), [L470](../src/delivery_schedule_optimization/engine.py#L470) |
| Stage 1: $\min P_u$ | `solve(None)` | [L478](../src/delivery_schedule_optimization/engine.py#L478) |
| Stage 2: the peak is fixed at $P_u^*$, then $\min \sum \kappa u$ | `solve(float(peak_after))` | [L480](../src/delivery_schedule_optimization/engine.py#L480) |

$\text{tol}$ is `smoothing_tolerance` (1 % by default). Option 0 of every couple always satisfies the budget, so the problem always has a solution. With at most about 60 couples × 10 options per plant, each problem is solved to optimality (gap 0) in under a second.

**What this approach gives up compared with one big model.** The peak only depends on the weekday sets, and the cost and trucks of each set are computed exactly. The second pass is therefore the exact optimum of the smoothing, under two simplifications:
1. the profile $N_c$ of each couple is fixed by the first pass;
2. for a given weekday set, the cheapest placement of the lines is kept. Lines are not moved between days only to smooth further.

### 6.4 Result

| Plant | Peak before (trucks/day) | Peak after | Extra cost |
| :--- | ---: | ---: | ---: |
| PLT_001 | 15 | 8 | +0.08 % |
| PLT_002 | 57 | 19 | +0.98 % |
| PLT_003 | 10 | 6 | +0.53 % |
| PLT_004 | 15 | 8 | +0.70 % |

86 couples change weekdays. The total cost goes from 109,623 € to 110,265 € (+0.6 %). Without smoothing, the first pass alone even raises the highest daily peak (54 trucks today, 57 after the first pass), because it gathers the deliveries on a few weekdays.

The notebook plots the trucks received per day before and after smoothing (section 5.2), and compares the current situation, the first pass and the smoothed plan in charts: total cost, deliveries, trucks, cost and peak per plant, trucks per weekday (section 6.1). Section 6 checks that no peak has increased.

---

## 7. Parameters

All parameters are in `OptimConfig` (notebook, section 0) and passed to the engine through `SolverSettings`.

| Parameter | Value | Effect |
| :--- | :--- | :--- |
| `truck_kg`, `truck_m3` | 24,500 kg, 110.4 m³ | $Q$ and $\bar V$, capacity of a truck (RG04). |
| `max_anticipation_days` | 7 | Size of the window $A_l$ (RG03). |
| `frequencies` | 1 … 5 | Profiles $N$ solved in the first pass. |
| `profile_tolerance` | 0 | Profile rule: smallest $N$ within this tolerance of the best cost (RG07, business choice). |
| `smoothing_tolerance` | 0.01 | Extra cost accepted per plant for the smoothing (RG09, business choice). |
| `delivery_penalty_eur` | 0.01 | $\varepsilon$: tie-break towards fewer deliveries. It is not included in the published cost. |
| `time_limit_s` | 5 | Time limit of each MILP. With 30 s, the cost changes by about 0.1 % and the run is 2.5 times longer. With 3 s, a large couple has no plan at $N = 1$. |
| `mip_gap` | 0.01 | The solver stops when its plan is proven within 1 % of the optimum. |
| `workers` | None | Number of parallel processes; `None` = one per CPU core. |

**Reproducibility.** When a solve stops on its time limit, the plan depends on the path the solver had taken at that moment. The results can therefore vary by about 0.1 % from one run to the next.

---

## 8. Validation and checks

| Check | Where |
| :--- | :--- |
| With a window of 0 days, the engine reproduces exactly the 1,665 current deliveries, and the milestone 2 estimator gives back 176,979 € | notebook, section 3 |
| Every line is delivered once (RG08), inside its window (RG03), on a working day (RG10) | notebook, section 6 |
| Every delivery is on an allowed weekday of its couple; a week never has more deliveries than the profile; the highest weekly frequency equals the profile (RG05) | notebook, section 6 |
| No truck is overloaded and every line is in one truck | `load_plan`, notebook, section 2.2 |
| The smoothing keeps every profile and never raises a plant's peak (RG09) | notebook, sections 5.2 and 6 |
| Output 2 loses no line and no weight | notebook, section 6.2 |

---

## 9. Running time

| Step | Time (12 cores) |
| :--- | ---: |
| Data, calendar, validation with a window of 0 days | about 5 s |
| First pass: 900 MILPs | about 105 s |
| Smoothing: about 1,100 weekday sets priced, then 4 plant MILPs | about 40 s |
| Loading, checks, exports | about 5 s |
| **Whole notebook** | **about 2 min 40 s** |
