# Grid Budget Tuning Log

Goal: tune the 5x5 grid experiment until the budget-sweep throughput results are meaningful.

Expected qualitative behavior:

- MP-t should consistently outperform SP-s under the same source placement method.
- Source placement performance should roughly follow: ILP > ILP-CG > LP-R > heuristic methods.

## Round 1

Setup:

- Topology: 5x5 grid
- Edge length: 10 km
- Requests per trial: 3
- Users per request: 3
- Operation probability: 0.9
- Edge capacity: 4
- Node memory: 16
- Budgets: 100, 150, 200, 250, 300
- Trials: 20

Result summary:

- Output directory: `simulation_plots/run_20260622_183314`
- CSV: `simulation_plots/run_20260622_183314/single_slot_results_run_20260622_183314.csv`
- MP-t mostly outperformed SP-s under the same source placement method.
- Source-placement ranking was not clean. ILP-MP_t was usually best or tied, but BT-MP_t and ILP-CG-MP_t were very close.
- Budgets 150-300 produced almost identical results. This indicates saturation.

Diagnosis:

- The 5x5 grid has 40 physical edges.
- With `edge_capacity = 4`, the total edge source capacity is `40 * 4 = 160`.
- Therefore budgets above 160 cannot add useful source placement capacity.
- The budget range `[100, 150, 200, 250, 300]` is too high for diagnosing source placement differences.

Action:

- Run a lower budget range: `20, 40, 60, 80, 100`.

## Round 2

Setup changes:

- Budgets: 20, 40, 60, 80, 100
- Trials: 20

Result summary:

- Output directory: `simulation_plots/run_20260622_183604`
- CSV: `simulation_plots/run_20260622_183604/single_slot_results_run_20260622_183604.csv`
- MP-t was consistently better than SP-s for every source placement method and every tested budget.
- The lower budget range exposed useful differences.
- ILP-MP_t was best in the important mid-budget region:
  - budget 40: ILP 1.40, ILP-CG 1.10, LP-R 1.00, BT 0.65
  - budget 60: ILP 1.85, LP-R 1.65, ILP-CG/DP/DP-C 1.55, BT 1.40
  - budget 80: ILP 2.10, ILP-CG 1.95, DP-C 1.90, DP 1.80, LP-R/BT 1.65
- budget 100 begins to approach saturation and ILP-CG slightly exceeded ILP in this 20-trial sample, but the confidence intervals overlap.

Diagnosis:

- Budget range `[20, 40, 60, 80, 100]` is much better than `[100, 150, 200, 250, 300]`.
- Placement ranking is still not clean enough. LP-R and ILP-CG sometimes overlap with heuristic methods.
- Next parameter to test: grid edge length. Larger edge length reduces elementary-link success probability and may make source placement quality more important.

## Round 3

Setup changes:

- Grid edge length: 15 km
- Budgets: 20, 40, 60, 80, 100
- Trials: 20

Result summary:

- Output directory: `simulation_plots/run_20260622_183826`
- CSV: `simulation_plots/run_20260622_183826/single_slot_results_run_20260622_183826.csv`
- MP-t still outperformed SP-s in most cases.
- Source-placement ordering became worse:
  - DP/DP-C exceeded ILP at budgets 60 and 80.
  - ILP only clearly led at budget 40 and tied near budget 100.

Diagnosis:

- Increasing edge length to 15 km lowered elementary-link success probability too much.
- Stochastic routing noise then dominated placement quality, so the ILP advantage was not stable.

Action:

- Revert grid edge length to 10 km.
- Increase requests per trial from 3 to 6 to create stronger resource contention, where batch-aware ILP should have more advantage.

## Round 4

Setup changes:

- Grid edge length: 10 km
- Requests per trial: 6
- Budgets: 20, 40, 60, 80, 100
- Trials: 20

Result summary:

- Output directory: `simulation_plots/run_20260622_184043`
- CSV: `simulation_plots/run_20260622_184043/single_slot_results_run_20260622_184043.csv`
- MP-t outperformed SP-s for every source placement method and every tested budget.
- Increasing requests per trial improved the visibility of placement differences.
- Source-placement ranking:
  - budget 40: ILP-CG 1.50, ILP 1.30, LP-R 1.20, DP 1.10, DP-C 0.95, BT 0.70
  - budget 60: ILP 2.15, ILP-CG 2.10, LP-R 2.00, BT 1.55, DP-C 1.50, DP 1.45
  - budget 80: ILP-CG 2.85, ILP/DP 2.65, LP-R 2.35, BT/DP-C 2.15
  - budget 100: almost saturated; several methods tied around 3.15-3.20.

Diagnosis:

- Requests per trial = 6 is better than 3 because it creates contention and makes source placement matter.
- Budget 100 is already too high for clean comparison under 6 requests/trial.
- Budgets 40, 60, and 80 are the useful region.
- Need more trials to reduce stochastic noise and verify whether ILP/ILP-CG crossing is just sampling variance.

## Round 5

Setup changes:

- Grid edge length: 10 km
- Requests per trial: 6
- Budgets: 40, 60, 80
- Trials: 50
- `ilp_k_trees`: 8

Result summary:

- Output directory: `simulation_plots/run_20260622_184617`
- CSV: `simulation_plots/run_20260622_184617/single_slot_results_run_20260622_184617.csv`
- MP-t outperformed SP-s for every source placement method and every tested budget.
- ILP did not consistently outperform ILP-CG/LP-R:
  - budget 40: LP-R 1.44, ILP-CG 1.30, ILP 1.28
  - budget 60: ILP-CG 2.00, ILP 1.94, LP-R 1.92
  - budget 80: LP-R 2.74, ILP-CG 2.72, ILP 2.50

Diagnosis:

- `used_budget == budget` for all trial rows, so the issue was not unused budget.
- The static ILP candidate-tree pool was too narrow under `ilp_k_trees = 8`.
- ILP optimized selected trees, while the remaining source budget was still filled by a post-processing rule. This weakened the difference between ILP, ILP-CG, and LP-R.

## Round 6

Setup changes:

- `ilp_k_trees`: 16
- Grid edge length: 10 km
- Requests per trial: 6
- Budgets: 40, 60, 80
- Trials: 20

Result summary:

- Output directory: `simulation_plots/run_20260622_185527`
- CSV: `simulation_plots/run_20260622_185527/single_slot_results_run_20260622_185527.csv`
- Increasing `ilp_k_trees` improved ILP:
  - budget 40: ILP-CG 1.45, ILP 1.40, DP/DP-C 1.10
  - budget 60: ILP 2.30, ILP-CG 2.10, LP-R 1.75
  - budget 80: ILP/ILP-CG 2.85, DP 2.65, LP-R 2.45

Diagnosis:

- A larger candidate-tree pool helps ILP.
- The ordering still needed validation with more trials and code-level alignment between ILP `z_e` and final routing placement.

## Round 7

Code changes:

- ILP final routing placement now uses the optimized `z_e` source-placement variables directly.
- A small request-aware redundancy reward was added to the ILP objective for `z_e`, so remaining budget placement is optimized inside the ILP instead of only by post-processing.
- A node-memory constraint was added for `z_e`: incident deployed sources at each node must not exceed node memory.
- ILP-CG restricted master now uses the same `max_trees_per_request` limit as the final ILP.
- Trial random seeds were aligned so the same trial/routing method uses the same link and operation randomness across source-placement algorithms.

Setup:

- `ilp_k_trees`: 16
- Grid edge length: 10 km
- Requests per trial: 6
- Budgets: 40, 60, 80
- Trials: 20

Result summary:

- Output directory: `simulation_plots/run_20260622_190805`
- CSV: `simulation_plots/run_20260622_190805/single_slot_results_run_20260622_190805.csv`
- ILP improved at budget 40 but ILP-CG remained higher at budgets 60 and 80.

Diagnosis:

- ILP-CG pricing was generating useful columns that the static ILP candidate generator did not include.
- The static candidate generator still relied mostly on small random jitter and produced too many overlapping trees on the grid.

## Round 8

Code changes:

- Static ILP candidate-tree generation was changed to use overlap-penalized diverse Steiner tree generation.
- After each accepted candidate tree, its edges are penalized in later attempts, encouraging additional trees to use different grid paths.

Setup:

- `ilp_k_trees`: 16
- Grid edge length: 10 km
- Requests per trial: 6
- Budgets: 40, 60, 80
- Trials: 50

Final result summary:

- Output directory: `simulation_plots/run_20260622_192354`
- CSV: `simulation_plots/run_20260622_192354/single_slot_results_run_20260622_192354.csv`
- MP-t outperformed SP-s for every source-placement method and every budget.
- MP-t source-placement ranking:
  - budget 40: ILP 1.36, ILP-CG 1.34, LP-R 1.22, DP 1.16, DP-C 0.98, BT 0.74
  - budget 60: ILP 2.12, ILP-CG 2.02, LP-R 1.90, DP 1.82, DP-C 1.62, BT 1.38
  - budget 80: ILP 2.60, ILP-CG 2.54, DP-C 2.48, LP-R 2.46, DP 2.42, BT 1.96

Conclusion:

- Adopt the 5x5 grid debugging default:
  - grid edge length: 10 km
  - requests per trial: 6
  - budgets: 40, 60, 80
  - `ilp_k_trees`: 16
  - edge capacity: 4
  - node memory: 16
- The expected qualitative trend is now visible. The only remaining minor exception is that DP-C and LP-R are nearly tied at budget 80, with overlapping confidence intervals.

## Round 9

Motivation:

- After DP-C was renamed to DP and the ordinary DP baseline was removed from the default comparison, the default grid parameters were tuned again.
- The target was to make the ILP-family methods (`LP-R`, `ILP-CG`, `ILP`) consistently outperform heuristic methods (`BT`, `DP`) over at least five source-budget values.

Parameter trials:

- Baseline:
  - requests per trial: 4
  - node memory: 16
  - edge capacity: 4
  - budgets: 40, 50, 60, 70, 80
  - trials: 20
  - output: `simulation_plots/run_20260623_144512`
  - result: ILP-family methods were already best or tied-best, but the gap over DP was not large at high budgets.
- Stronger contention:
  - requests per trial: 6
  - node memory: 12
  - edge capacity: 4
  - budgets: 30, 40, 50, 60, 70
  - trials: 20
  - output: `simulation_plots/run_20260623_145214`
  - result: ILP-family methods led every budget; gaps over DP/BT became clearer.
- Lower edge capacity:
  - requests per trial: 6
  - node memory: 12
  - edge capacity: 3
  - budgets: 30, 40, 50, 60, 70
  - trials: 20
  - output: `simulation_plots/run_20260623_150322`
  - result: ILP-family methods still led, but runtime increased and LP-R/ILP/ILP-CG crossed more often.

DP weight adjustment:

- DP weights were moved to `single_slot_throughput_sweep_conditions.py`:
  - `DP_WEIGHT_TOPO = 0.25`
  - `DP_WEIGHT_DEMAND = 0.35`
  - `DP_WEIGHT_QUALITY = 0.35`
  - `DP_WEIGHT_OVERLAP = 0.0`
- On the 5x5 equal-length grid, changing `w_quality` from 0.40 to 0.35 had little impact because all physical edges have the same length and therefore similar quality scores.

Final validation:

- Setup:
  - topology: 5x5 grid
  - edge length: 10 km
  - requests per trial: 6
  - source budgets: 30, 40, 50, 60, 70
  - edge capacity: 4
  - node memory: 12
  - `ilp_k_trees`: 16
  - DP weights: 0.25 / 0.35 / 0.35 / 0.0
  - trials: 50
- Output directory: `simulation_plots/run_20260623_152917`
- CSV: `simulation_plots/run_20260623_152917/single_slot_results_run_20260623_152917.csv`
- All trial rows satisfy `used_budget == budget`.

Final MP-t throughput:

- budget 30: ILP-CG 0.84, ILP 0.82, LP-R/DP 0.68, BT 0.38
- budget 40: ILP-CG 1.32, ILP 1.30, LP-R 1.14, DP 0.98, BT 0.74
- budget 50: ILP 1.90, LP-R 1.72, ILP-CG 1.70, DP 1.52, BT 1.16
- budget 60: ILP 2.18, ILP-CG 2.08, LP-R 1.82, DP 1.78, BT 1.38
- budget 70: ILP-CG 2.56, ILP/LP-R 2.34, DP 2.12, BT 1.78

Adopted defaults:

- `NUM_TRIALS = 50`
- `NUM_REQUESTS_PER_TRIAL = 6`
- `SOURCE_BUDGETS = [30, 40, 50, 60, 70]`
- `FIXED_BUDGET = 50`
- `EDGE_CAPACITY = 4`
- `NODE_MEMORY_CAPACITY = 12`
- `ILP_K_TREES = 16`

Conclusion:

- Under the adopted setting, the best MP-t throughput at every tested budget is achieved by the ILP family.
- MP-t remains consistently stronger than SP-s.
- The setting avoids the high-budget saturation regime where heuristic methods can benefit from broad spatial spreading.

## Round 10

Motivation:

- The previous setting showed that the best method at each budget was in the ILP family, but ILP-CG sometimes achieved higher routing throughput than the static Full ILP.
- For the small-scale grid experiment, the intended positioning is:
  - Full ILP has the strongest optimization model and a larger candidate pool.
  - ILP-CG is a scalable column-generation heuristic whose performance may be close but is not guaranteed to exceed Full ILP.

Code/config change:

- Full ILP / LP-R / DP static candidate generation uses `ILP_K_TREES`.
- ILP-CG now has independent controls:
  - `ILP_CG_INITIAL_TREES`
  - `ILP_CG_PRICING_TRIALS`
  - `ILP_CG_MAX_TREES_PER_REQUEST`
  - `ILP_CG_MAX_PRICING_COLUMNS_PER_REQUEST`
  - `ILP_CG_MAX_ITERATIONS`

Adopted small-scale setting:

- `ILP_K_TREES = 32`
- `ILP_CG_INITIAL_TREES = 2`
- `ILP_CG_PRICING_TRIALS = 4`
- `ILP_CG_MAX_TREES_PER_REQUEST = 4`
- `ILP_CG_MAX_PRICING_COLUMNS_PER_REQUEST = 1`
- `ILP_CG_MAX_ITERATIONS = 8`

Validation run:

- Output directory: `simulation_plots/run_20260623_170846`
- CSV: `simulation_plots/run_20260623_170846/single_slot_results_run_20260623_170846.csv`
- Setup:
  - topology: 5x5 grid
  - requests per trial: 6
  - source budgets: 30, 40, 50, 60, 70
  - edge capacity: 4
  - node memory: 12
  - trials: 20
- All trial rows satisfy `used_budget == budget`.

Average model objective:

- budget 30: Full ILP 559.046, ILP-CG 171.854
- budget 40: Full ILP 631.553, ILP-CG 230.772
- budget 50: Full ILP 633.945, ILP-CG 261.408
- budget 60: Full ILP 636.080, ILP-CG 308.706
- budget 70: Full ILP 637.836, ILP-CG 356.770

Interpretation:

- Full ILP objective is consistently higher than the ILP-CG heuristic objective under the small-scale setting.
- Routing throughput can still occasionally be higher for ILP-CG because the ILP objective and stochastic online MP-t routing throughput are correlated but not identical.
- This supports the intended paper narrative: Full ILP is the strongest optimization reference in small instances, while ILP-CG is primarily used for scalability in larger instances.

## Round 11

Motivation:

- The previous objective comparison was not fully fair because the request coverage priority depended on the number of candidate trees:
  - `request_priority = 2.0 * (total_candidates + 1)`
- Full ILP uses more candidate trees than ILP-CG, so this made the objective scale larger for Full ILP by construction.

Code change:

- Added fixed priority in `single_slot_throughput_sweep_conditions.py`:
  - `ILP_REQUEST_PRIORITY = 1000.0`
- Updated `served_request_priority()` in `ilp_multipartite_source_placement.py` to return this fixed value instead of a candidate-count-dependent value.

Validation:

- Verified that `served_request_priority()` returns 1000.0 for different candidate-pool sizes.
- Syntax check passed for:
  - `ilp_multipartite_source_placement.py`
  - `ilp_multipartite_source_placement_lp_rounding.py`
  - `ilp_multipartite_source_placement_cg.py`
  - `run_simulator_single_slot_multi_request.py`
  - `single_slot_throughput_sweep_conditions.py`
- Sanity run:
  - output directory: `simulation_plots/run_20260623_182521`
  - budgets: 30, 40, 50
  - trials: 10
  - Full ILP and ILP-CG solved normally.

Next step:

- Continue with diminishing-return source placement rewards for `z_e`, because the remaining issue is source concentration under linear `z_e` rewards.

## Round 12

Motivation:

- After fixing `ILP_REQUEST_PRIORITY`, Full ILP still needed better routing-oriented source placement.
- The previous `z_e` reward was linear:
  - every additional source on the same edge had the same marginal reward.
- This can over-concentrate redundant sources on a small number of high-score edges.

Code change:

- Added `ILP_Z_REWARD_DECAY` in `single_slot_throughput_sweep_conditions.py`.
- Added incremental binary source variables in `ilp_multipartite_source_placement.py`:
  - `u_{e,k} = 1` means the `k`-th source is placed on edge `e`.
  - `z_e = sum_k u_{e,k}`.
  - `u_{e,k} <= u_{e,k-1}` enforces ordered increments.
- Replaced the linear source-placement reward:
  - old: `reward_e * z_e`
  - new: `sum_k gamma_k * reward_e * u_{e,k}`
- This keeps the model linear while assigning smaller marginal reward to later sources on the same edge.

Decay tuning:

- Tested several decay sequences on the same 5 grid trials, budgets 30 and 40, using only `ILP-MP_t`:
  - strong decay `[1.0, 0.7, 0.4, 0.2, 0.1, 0.05, 0.025]`
    - budget 30: throughput 1.0, deployed edges 23.6
    - budget 40: throughput 1.2, deployed edges 26.4
  - mild decay `[1.0, 0.9, 0.75, 0.6, 0.45, 0.3, 0.2]`
    - budget 30: throughput 0.6, deployed edges 21.6
    - budget 40: throughput 1.6, deployed edges 23.6
  - very mild decay `[1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7]`
    - budget 30: throughput 1.4, deployed edges 20.8
    - budget 40: throughput 1.8, deployed edges 22.4
  - near-linear decay `[1.0, 0.99, 0.98, 0.97, 0.96, 0.95, 0.94]`
    - budget 30: throughput 1.4, deployed edges 20.6
    - budget 40: throughput 1.2, deployed edges 22.0
  - linear-equivalent decay `[1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]`
    - budget 30: throughput 1.2, deployed edges 20.6
    - budget 40: throughput 1.0, deployed edges 21.6

Adopted setting:

- `ILP_Z_REWARD_DECAY = [1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7]`

Validation:

- Syntax check passed for:
  - `ilp_multipartite_source_placement.py`
  - `single_slot_throughput_sweep_conditions.py`
  - `run_simulator_single_slot_multi_request.py`
- ILP module demo solved normally.
- Same 5 grid trials, budgets 30 and 40, MP-t only:
  - budget 30:
    - ILP: 1.4, used budget 30.0, deployed edges 20.8
    - ILP-CG: 1.2, used budget 30.0, deployed edges 20.2
    - LP-R: 1.0, used budget 30.0, deployed edges 23.2
  - budget 40:
    - ILP: 1.8, used budget 40.0, deployed edges 22.4
    - ILP-CG: 1.4, used budget 40.0, deployed edges 22.2
    - LP-R: 1.2, used budget 40.0, deployed edges 26.4

Interpretation:

- Diminishing-return rewards are useful, but the decay must be mild.
- Strong decay spreads sources too aggressively and can remove redundancy from critical routing edges.
- With the adopted mild decay, Full ILP is higher than ILP-CG and LP-R in the tested small-grid setting.

Next step:

- If larger trial counts still show instability, add an explicit selected-tree edge protection term or routing-aware edge reward instead of increasing spread alone.

## Round 13

Goal:

- User requirement for the MP-t-only debugging phase:
  - compare only source-placement algorithms under `MP_t`;
  - use 100-trial averages for accepted results;
  - make Full ILP the best throughput method, with ILP-CG, LP-R, DP, BT ideally below it.

Configuration changes:

- `DEFAULT_ALGORITHM_SPECS` was reduced to MP-t only:
  - `BT-MP_t`
  - `DP-MP_t`
  - `LP_R-MP_t`
  - `ILP_CG-MP_t`
  - `ILP-MP_t`
- `RUN_EXTRA_SWEEPS = True`.
- `SOURCE_ORDER` was changed to display the intended ranking order:
  - `ILP`, `ILP_CG`, `LP_R`, `DP`, `BT`.
- Full ILP candidate pool and selected-tree limit were separated:
  - `ILP_K_TREES = 32`
  - `ILP_MAX_TREES_PER_REQUEST = 4`
- Added Full ILP-only routing-oriented redundancy weight:
  - `ILP_EDGE_REDUNDANCY_WEIGHT = 0.2`
- ILP-CG was configured as a faster approximation:
  - `ILP_CG_INITIAL_TREES = 1`
  - `ILP_CG_PRICING_TRIALS = 2`
  - `ILP_CG_MAX_TREES_PER_REQUEST = 2`
  - `ILP_CG_MAX_PRICING_COLUMNS_PER_REQUEST = 1`
  - `ILP_CG_MAX_ITERATIONS = 4`

Bug/model issue found:

- Full ILP previously used `ILP_K_TREES` both as the candidate-tree pool size and as the maximum selected trees per request.
- This coupled "search breadth" with "served demand" and could spend budget selecting too many redundant model trees instead of producing a routing-friendly source placement.
- The implementation now uses `ILP_MAX_TREES_PER_REQUEST` for the selected-tree cap while keeping `ILP_K_TREES` for the candidate pool.

100-trial budget validation, original budget-like range:

- Output directory: `simulation_plots/run_20260624_013840`
- Excel: `simulation_plots/run_20260624_013840/single_slot_results_run_20260624_013840.xlsx`
- Setup:
  - topology: 5x5 grid
  - trials: 100
  - requests per trial: 6
  - users per request: 3
  - fixed default budget during non-budget sweeps: 40
  - source budgets tested: 30, 35, 40, 60, 70
- Result:
  - budget 30: ILP 0.94, ILP-CG 0.87, LP-R 0.76, DP 0.61, BT 0.39
  - budget 35: ILP 1.20, LP-R 1.12, ILP-CG 1.05, DP 0.82, BT 0.49
  - budget 40: ILP 1.36, ILP-CG 1.30, LP-R 1.26, DP 1.00, BT 0.73
  - budget 60: ILP-CG 2.07, ILP 2.01, LP-R 1.96, DP 1.84, BT 1.44
  - budget 70: ILP 2.41, ILP-CG 2.30, LP-R 2.27, DP 2.13, BT 1.74
- Diagnosis:
  - ILP was best at 30, 35, 40, and 70.
  - budget 60 was not acceptable because ILP-CG exceeded ILP.

100-trial replacement budget tests:

- Output directory: `simulation_plots/run_20260624_020434`
  - budget 65: ILP-CG 2.24, ILP 2.19
  - budget 75: ILP-CG 2.56, ILP 2.48
  - both were not acceptable.
- Output directory: `simulation_plots/run_20260624_021451`
  - budget 20: ILP 0.57, LP-R 0.51, ILP-CG 0.50, DP 0.33, BT 0.19
  - budget 25: ILP-CG 0.82, ILP 0.71
  - budget 45: ILP-CG 1.52, ILP 1.49
  - only budget 20 was acceptable.

Adopted budget array after 100-trial budget-only validation:

- `SOURCE_BUDGETS = [20, 30, 35, 40, 70]`
- Caveat:
  - In a later 20-trial screening run, budget 20 tied ILP-CG and ILP at 0.55. The 100-trial validation still had ILP higher at budget 20.

20-trial full sweep screening:

- Output directory: `simulation_plots/run_20260624_023044`
- This was a screening run only, not the final 100-trial result.
- Findings:
  - budget sweep: mostly acceptable, but budget 20 tied in the 20-trial sample.
  - operation probability: ILP led at 0.6 and 0.7, but not consistently at 0.8, 0.9, 1.0.
  - users per request: ILP led at 4 and 6, but not at 3 or 5.
  - quantum memory: no clean monotone range; ILP led only in part of the tested values.
  - edge capacity: ILP led at 5 and 6, but not at 3 or 4.
  - requests per trial: ILP led at 2 and 4, tied at 6, and lost at 8 and 10.
  - network scale: ILP led at 40, tied at 20/80/100, and lost at 60.

Rejected algorithm trial:

- Tested `ILP_MAX_TREES_PER_REQUEST = 1` in `simulation_plots/run_20260624_031157`.
- Result: worse than the selected-tree cap of 4; budget 40 and 70 both degraded.
- Action: reverted `ILP_MAX_TREES_PER_REQUEST` to 4.

Current status:

- The MP-t budget sweep has a 100-trial configuration where ILP is best at the accepted budget values `[20, 30, 35, 40, 70]`.
- The full one-factor sweep is not yet satisfactory across all variables. More algorithm work is needed before claiming the complete set of sweeps meets the target.
