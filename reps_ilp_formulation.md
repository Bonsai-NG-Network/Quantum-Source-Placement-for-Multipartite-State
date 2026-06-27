# REPS-Style Multipartite Source Provisioning ILP

This repository uses the ILP as a source/link provisioning model, not as the
final GHZ routing model.

## Stage Semantics

1. ILP stage: choose edge-level source/link-generation attempts `z_e`.
2. Realization stage: each deployed attempt on edge `e` succeeds independently
   with probability `p_e`.
3. Routing stage: run the online multipartite routing-packing algorithm. A
   request may generate multiple GHZ states in one slot if enough realized
   Bell-link resources remain.
4. Evaluation stage: count throughput as the number of successfully generated
   GHZ states per time slot.

The ILP's candidate-tree variables are provisioning-stage service units. They
are not final realized routing trees.

Because `x_{r,t}` is integer and there is no per-request demand cap, routing
evaluation must also use request-level packing semantics. The default
single-slot plots therefore include:

- `singlepath_star_packing` (`SP_s_p`);
- `multipath_tree_packing` (`MP_t_p`).

The two routing methods differ in where the route is selected:

- `singlepath_star_packing`: the center and star paths are selected on the
  original physical topology `G`. The selected star paths are fixed for the
  request, and packing repeatedly consumes realized Bell links along those
  fixed paths until one required path lacks an active Bell link.
- `multipath_tree_packing`: every packing attempt selects a tree on the current
  remaining realized Bell-link graph `G_prime`. After each successful or failed
  operation, `G_prime` is rebuilt and the algorithm continues until no
  connecting tree exists for the request terminals.

## Sets

- Physical network: `G = (V, E)`.
- Multipartite requests: `R`.
- Terminal set of request `r`: `S_r`.
- Candidate tree set for request `r`: `T_r`.
- Edge set of candidate tree `t`: `E_t`.
- Swapping-node set of candidate tree `t`: `V_t^swap`.
- Fusion-node set of candidate tree `t`: `V_t^fus`.

## Parameters

- `B`: total source budget.
- `C_s`: cost of one source/link-generation attempt.
- `Z_e^max`: maximum attempts allowed on edge `e`.
- `M_v`: node memory capacity.
- `p_e`: Bell-link generation probability on edge `e`.
- `q_v^swap`: swapping success probability at node `v`.
- `q_v^fus`: fusion success probability at node `v`.
- `a_{e,t}`: 1 if tree `t` uses edge `e`, otherwise 0.
- `rho_op[r,t]`: operation-level success probability:
  `prod_{v in V_t^swap} q_v^swap * prod_{v in V_t^fus} q_v^fus`.

`rho_op[r,t]` intentionally excludes edge generation probability. Link
generation appears only in the expected edge-capacity constraint.

## Decision Variables

- `z_e in Z_+`: number of source/link-generation attempts deployed on edge `e`.
- `x_{r,t} in Z_+`: number of provisioned multipartite service units for
  request `r` using candidate tree pattern `t`.

## Model

Maximize:

```text
sum_{r in R} sum_{t in T_r} rho_op[r,t] x_{r,t}
```

Subject to:

```text
sum_{e in E} C_s z_e <= B
```

```text
0 <= z_e <= Z_e^max                        for all e in E
```

```text
sum_{r in R} sum_{t in T_r} a_{e,t} x_{r,t} <= p_e z_e
                                             for all e in E
```

```text
sum_{e incident to v} z_e <= M_v            for all v in V
```

The node-memory constraint limits incident source/link-generation attempts. It
does not model tree-level memory consumption or final GHZ-state storage.

The current ILP formulation does not include a per-request demand bound. In
particular, there is no constraint of the form:

```text
sum_{t in T_r} x_{r,t} <= D_r               for all r in R
```

Thus, `x_{r,t}` service-unit provisioning is limited by the global source
budget, per-edge source limits, expected edge capacity, and node memory, not by
an explicit per-request cap. This applies to the Full ILP and to the current
LP-R/ILP-CG variants used for comparison.

## Removed Legacy Semantics

The current ILP does not use:

- request coverage variables `y_r`;
- request coverage lower/upper constraints;
- binary-only `x_{r,t}`;
- request priority weights `w_r`;
- coverage-first objective terms;
- edge-redundancy reward terms;
- leftover-budget post-processing.

Redundancy in this model means extra parallel link-generation attempts encoded
directly by larger `z_e` values, constrained by budget, edge limits, expected
Bell-link capacity, and node memory.

## Solver Status And Validation

Full ILP is exact only over the pre-generated candidate tree set. A result is
reported as exact only when Gurobi returns `OPTIMAL`. If Gurobi stops with a
time limit or suboptimal incumbent, the result is reported as `best_incumbent`
with the objective bound and MIP gap when available.

Full ILP, ILP-CG final integer RMP, and LP-R rounded outputs are validated
against:

```text
sum_e C_s z_e <= B
0 <= z_e <= Z_e^max
sum_{r,t} a_{e,t} x_{r,t} <= p_e z_e       for all e in E
sum_{e incident to v} z_e <= M_v           for all v in V
z_e, x_{r,t} are nonnegative integers
```

The validation result is returned in `feasibility_check`.

## Scalable Variants

ILP-CG solves LP-relaxed restricted master problems using the same REPS
constraints and prices new columns with:

```text
reduced_cost(r,t) = rho_op[r,t] - sum_{e in E_t} lambda_e
```

Only expected edge-capacity duals `lambda_e` enter pricing. The final step
solves an integer RMP over generated columns. Without branch-and-price, ILP-CG
is an approximate method and is not guaranteed to equal the Full ILP integer
optimum.

LP-R solves the LP relaxation with both `x_{r,t}` and `z_e` continuous. Rounding
is service-unit-first: it rounds provisioned service units, maintains edge
demand `d_e`, derives `z_e = ceil(d_e / p_e)`, and accepts each tentative unit
only if all original integer constraints remain feasible. LP-R returns both the
LP relaxation objective and the rounded integer provisioning objective.

## Implementation Call Relationship

The 100-trial sweep entry point is:

```text
run_simulator_single_slot_multi_request.py::main()
```

The main call path is:

```text
main()
  -> parse_args()
  -> build_topology_from_args()
  -> conditions.SWEEP_CONDITIONS
  -> evaluate_algorithms_over_one_factor_sweep()
      -> generate_request_batches()
      -> truncate_request_batches()
      -> evaluate_algorithms()
          -> run_algorithm_on_single_slot_batch()
              -> place_sources_for_batch()
              -> deploy_elementary_links_once()
              -> run_request_once_in_current_slot()
```

Source-placement dispatch in `place_sources_for_batch()`:

```text
BETWEENNESS -> quantum_source_placement_betweenness.py
DP          -> quantum_source_placement_dp.py plus candidate trees from
               ilp_multipartite_source_placement.py
LP_ROUND    -> ilp_multipartite_source_placement_lp_rounding.py
ILP_CG      -> ilp_multipartite_source_placement_cg.py
ILP         -> ilp_multipartite_source_placement.py
```

Full ILP call path:

```text
place_sources_for_batch()
  -> solve_single_slot_ilp_request_batch()
      -> requests_from_user_sets()
      -> build_candidate_trees_for_requests()
      -> solve_joint_source_placement_ilp()
```

LP-R call path:

```text
place_sources_for_batch()
  -> solve_single_slot_lp_rounding_request_batch()
      -> build_candidate_trees_for_requests()
      -> solve_source_placement_lp_relaxation()
      -> round_lp_solution_to_source_placement()
```

ILP-CG call path:

```text
place_sources_for_batch()
  -> solve_single_slot_ilp_cg_request_batch()
      -> build_candidate_trees_for_requests()
      -> solve_joint_source_placement_ilp()
```

With `ILP_CG_USE_NESTED_POOL = True`, the current ILP-CG implementation uses a
restricted nested candidate-tree pool and then solves the same REPS ILP on that
restricted pool. This keeps ILP-CG below the Full ILP in model scope while still
using the same source-budget, expected edge-capacity, node-memory, and
no-per-request-demand semantics.

Routing dispatch:

```text
run_request_once_in_current_slot()
  -> singlepath_star_packing:
       SPEntanglementRouting.singlepath_star_packing_routing()
  -> multipath_tree_packing:
       MultipathTreePackingRouting.multipath_tree_packing_routing()
```
