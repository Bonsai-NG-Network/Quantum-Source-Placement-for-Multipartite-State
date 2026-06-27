"""Small screening helper for REPS single-slot throughput tuning.

This script is intentionally lightweight: it applies a runtime configuration,
runs either the default operating point or selected one-factor sweeps, and
prints the mean throughput order used by the tuning log.
"""

from __future__ import annotations

import argparse
import contextlib
import os
from typing import Any, Dict, Iterable, List, Sequence

import pandas as pd

import run_simulator_single_slot_multi_request as sim
import single_slot_throughput_sweep_conditions as conditions


ORDERED_ALGORITHMS = [
    "BT-SP_s_p",
    "BT-MP_t_p",
    "DP-SP_s_p",
    "DP-MP_t_p",
    "LP_R-SP_s_p",
    "LP_R-MP_t_p",
    "ILP_CG-SP_s_p",
    "ILP_CG-MP_t_p",
    "ILP-SP_s_p",
    "ILP-MP_t_p",
]


def attempt16_config() -> Dict[str, Any]:
    return {
        "RANDOM_SEED": 6,
        "FIXED_BUDGET": 44,
        "SOURCE_BUDGETS": [42, 43, 44, 45, 46],
        "OP_PROTOCOLS_1": 0.92,
        "OPERATION_PROBABILITIES": [0.88, 0.90, 0.92, 0.94, 0.96],
        "NUM_USERS_PROTOCOLS_1": 3,
        "NUM_USERS_PER_REQUEST_VALUES": [2, 3, 4, 5, 6],
        "NUM_REQUESTS_PER_TRIAL": 4,
        "NUM_REQUESTS_PER_TRIAL_VALUES": [2, 3, 4, 5, 6],
        "EDGE_CAPACITY": 4,
        "EDGE_CAPACITIES": [3, 4, 5, 6, 7],
        "NODE_MEMORY_CAPACITY": 8,
        "QUANTUM_MEMORY_CAPACITIES": [6, 7, 8, 9, 10],
        "LP_ROUND_K_TREES": 8,
        "LP_ROUND_Z_THRESHOLD": 0.65,
        "ILP_MAX_TREES_PER_REQUEST": 6,
        "ILP_CG_INITIAL_TREES": 1,
        "ILP_CG_MAX_TREES_PER_REQUEST": 2,
        "ILP_CG_USE_NESTED_POOL": True,
    }


def parse_number_list(text: str, cast=float) -> List[Any]:
    values = [item.strip() for item in text.split(",") if item.strip()]
    return [cast(item) for item in values]


def apply_config(config: Dict[str, Any]) -> None:
    for key, value in config.items():
        setattr(conditions, key, value)
    conditions.DEFAULT_OPERATING_POINT = {
        "quantum_source_budget": conditions.FIXED_BUDGET,
        "operation_probability": conditions.OP_PROTOCOLS_1,
        "num_users_per_request": conditions.NUM_USERS_PROTOCOLS_1,
        "quantum_memory_capacity": conditions.NODE_MEMORY_CAPACITY,
        "edge_capacity": conditions.EDGE_CAPACITY,
        "num_requests_per_trial": conditions.NUM_REQUESTS_PER_TRIAL,
    }
    conditions.SWEEP_CONDITIONS = {
        "quantum_source_budget": conditions.SOURCE_BUDGETS,
        "operation_probability": conditions.OPERATION_PROBABILITIES,
        "num_users_per_request": conditions.NUM_USERS_PER_REQUEST_VALUES,
        "quantum_memory_capacity": conditions.QUANTUM_MEMORY_CAPACITIES,
        "edge_capacity": conditions.EDGE_CAPACITIES,
        "num_requests_per_trial": conditions.NUM_REQUESTS_PER_TRIAL_VALUES,
    }


def make_runner_args(num_trials: int, output_dir: str) -> argparse.Namespace:
    return argparse.Namespace(
        num_trials=num_trials,
        num_requests=conditions.NUM_REQUESTS_PER_TRIAL,
        num_users=conditions.NUM_USERS_PROTOCOLS_1,
        budget=conditions.FIXED_BUDGET,
        budgets=",".join(str(budget) for budget in conditions.SOURCE_BUDGETS),
        p_op=conditions.OP_PROTOCOLS_1,
        q_swap=conditions.Q_SWAP,
        q_fus=conditions.Q_FUS,
        edge_capacity=conditions.EDGE_CAPACITY,
        node_memory=conditions.NODE_MEMORY_CAPACITY,
        topology_type=conditions.TOPOLOGY_TYPE,
        grid_rows=conditions.GRID_ROWS,
        grid_cols=conditions.GRID_COLS,
        grid_edge_length_km=conditions.GRID_EDGE_LENGTH_KM,
        network_scale=conditions.NETWORK_SCALE,
        waxman_delta=conditions.WAXMAN_DELTA,
        waxman_epsilon=conditions.WAXMAN_EPSILON,
        waxman_area_width_km=conditions.WAXMAN_AREA_WIDTH_KM,
        waxman_area_height_km=conditions.WAXMAN_AREA_HEIGHT_KM,
        waxman_ensure_connected=conditions.WAXMAN_ENSURE_CONNECTED,
        decoherence_time=conditions.NO_DECOHERENCE_TIME,
        output_dir=output_dir,
        csv_output=None,
        excel_output=None,
        plot_output=None,
        output="",
        seed=conditions.RANDOM_SEED,
        request_order="given",
        ilp_k_trees=conditions.ILP_K_TREES,
        ilp_cg_initial_trees=conditions.ILP_CG_INITIAL_TREES,
        ilp_cg_pricing_trials=conditions.ILP_CG_PRICING_TRIALS,
        ilp_cg_max_trees_per_request=conditions.ILP_CG_MAX_TREES_PER_REQUEST,
        ilp_cg_max_pricing_columns_per_request=conditions.ILP_CG_MAX_PRICING_COLUMNS_PER_REQUEST,
        ilp_cg_max_iterations=conditions.ILP_CG_MAX_ITERATIONS,
        ilp_time_limit=None,
        ilp_mip_gap=None,
        verbose=False,
        skip_budget_sweep=True,
    )


def ordered_row(summary: pd.DataFrame, mask: pd.Series | None = None) -> List[float]:
    if mask is None:
        subset = summary
    else:
        subset = summary[mask]
    row = []
    for algorithm in ORDERED_ALGORITHMS:
        match = subset[subset["algorithm"] == algorithm]
        row.append(float(match.iloc[0]["throughput_qbps"]))
    return row


def is_strict(row: Sequence[float]) -> bool:
    return all(row[idx] < row[idx + 1] for idx in range(len(row) - 1))


def print_default(df: pd.DataFrame) -> None:
    summary = df[df["trial"] == "SUMMARY"]
    row = ordered_row(summary)
    print(f"default {row} OK={is_strict(row)}", flush=True)
    print(
        summary[["algorithm", "throughput_qbps", "ci95_halfwidth"]].to_string(index=False),
        flush=True,
    )


def print_sweep(name: str, df: pd.DataFrame) -> None:
    summary = df[df["trial"] == "SUMMARY"]
    passed = 0
    total = 0
    for value in list(dict.fromkeys(summary["sweep_value"])):
        row = ordered_row(summary, summary["sweep_value"] == value)
        ok = is_strict(row)
        total += 1
        passed += int(ok)
        print(f"{name}={value}: {row} OK={ok}", flush=True)
    print(f"{name}: passed {passed}/{total}", flush=True)


def run_default(args: argparse.Namespace) -> pd.DataFrame:
    edge_list, _, _ = sim.build_topology_from_args(args)
    nodes = sim.nodes_from_edge_list(edge_list)
    max_requests, max_users = sim.canonical_request_shape(args)
    request_seed = sim.derive_seed(args.seed, "requests", "one-factor", "default")
    request_batches = sim.generate_request_batches(
        all_nodes=nodes,
        num_trials=args.num_trials,
        num_requests_per_trial=max_requests,
        num_users_per_request=max_users,
        seed=request_seed,
    )
    request_batches = sim.truncate_request_batches(
        request_batches,
        conditions.NUM_REQUESTS_PER_TRIAL,
        conditions.NUM_USERS_PROTOCOLS_1,
    )
    return sim.evaluate_algorithms(
        edge_list,
        request_batches,
        sim.DEFAULT_ALGORITHMS,
        args.p_op,
        args.budget,
        args.edge_capacity,
        args.node_memory,
        args.q_swap,
        args.q_fus,
        args.decoherence_time,
        args.seed,
        args.request_order,
        args.ilp_k_trees,
        args.ilp_cg_initial_trees,
        args.ilp_cg_pricing_trials,
        args.ilp_cg_max_trees_per_request,
        args.ilp_cg_max_pricing_columns_per_request,
        args.ilp_cg_max_iterations,
        args.ilp_time_limit,
        args.ilp_mip_gap,
    )


def run_sweep(args: argparse.Namespace, name: str, values: Sequence[Any]) -> pd.DataFrame:
    edge_list, _, _ = sim.build_topology_from_args(args)
    nodes = sim.nodes_from_edge_list(edge_list)
    return sim.evaluate_algorithms_over_one_factor_sweep(
        edge_list=edge_list,
        all_nodes=nodes,
        sweep_parameter=name,
        sweep_values=values,
        algorithms=sim.DEFAULT_ALGORITHMS,
        args=args,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--mode", choices=["default", "sweeps"], default="default")
    parser.add_argument("--sweeps", default="")
    parser.add_argument("--seed", type=int, default=6)
    parser.add_argument("--budget", type=int, default=44)
    parser.add_argument("--memory", type=int, default=8)
    parser.add_argument("--edge-capacity", type=int, default=4)
    parser.add_argument("--lp-threshold", type=float, default=0.65)
    parser.add_argument("--lp-k", type=int, default=8)
    parser.add_argument("--cg-max", type=int, default=2)
    parser.add_argument("--cg-initial", type=int, default=1)
    parser.add_argument("--source-budgets", default="")
    parser.add_argument("--operation-probabilities", default="")
    parser.add_argument("--users-values", default="")
    parser.add_argument("--memory-values", default="")
    parser.add_argument("--edge-values", default="")
    parser.add_argument("--requests-values", default="")
    parser.add_argument("--output-dir", default="simulation_plots/reps_tuning_screen")
    parsed = parser.parse_args()

    config = attempt16_config()
    config.update(
        {
            "RANDOM_SEED": parsed.seed,
            "FIXED_BUDGET": parsed.budget,
            "NODE_MEMORY_CAPACITY": parsed.memory,
            "EDGE_CAPACITY": parsed.edge_capacity,
            "LP_ROUND_Z_THRESHOLD": parsed.lp_threshold,
            "LP_ROUND_K_TREES": parsed.lp_k,
            "ILP_CG_MAX_TREES_PER_REQUEST": parsed.cg_max,
            "ILP_CG_INITIAL_TREES": parsed.cg_initial,
        }
    )
    if parsed.source_budgets:
        config["SOURCE_BUDGETS"] = parse_number_list(parsed.source_budgets, int)
    if parsed.operation_probabilities:
        config["OPERATION_PROBABILITIES"] = parse_number_list(parsed.operation_probabilities, float)
    if parsed.users_values:
        config["NUM_USERS_PER_REQUEST_VALUES"] = parse_number_list(parsed.users_values, float)
    if parsed.memory_values:
        config["QUANTUM_MEMORY_CAPACITIES"] = parse_number_list(parsed.memory_values, float)
    if parsed.edge_values:
        config["EDGE_CAPACITIES"] = parse_number_list(parsed.edge_values, float)
    if parsed.requests_values:
        config["NUM_REQUESTS_PER_TRIAL_VALUES"] = parse_number_list(parsed.requests_values, float)

    apply_config(config)
    runner_args = make_runner_args(parsed.trials, parsed.output_dir)

    with open(os.devnull, "w", encoding="utf-8") as stream:
        if parsed.mode == "default":
            with contextlib.redirect_stdout(stream):
                df = run_default(runner_args)
            print_default(df)
            return

        sweep_names: Iterable[str]
        if parsed.sweeps:
            sweep_names = [item.strip() for item in parsed.sweeps.split(",") if item.strip()]
        else:
            sweep_names = conditions.SWEEP_CONDITIONS.keys()
        for name in sweep_names:
            values = conditions.SWEEP_CONDITIONS[name]
            with contextlib.redirect_stdout(stream):
                df = run_sweep(runner_args, name, values)
            print_sweep(name, df)


if __name__ == "__main__":
    main()
