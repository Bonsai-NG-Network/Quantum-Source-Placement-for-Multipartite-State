"""
Multi-request adapter for the original simulation algorithms.

The original simulator treats one trial as one multipartite request:
    trial i -> user_set_i

The journal ILP setting treats one trial as multiple concurrent requests:
    trial i -> [user_set_i_0, user_set_i_1, ...]

This file keeps the original EventSimulator / source-placement / routing code
unchanged, but wraps it so every compared algorithm receives the same
pre-generated request batches.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import random
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

import run_simulator_3 as base
from entanglement_distribution import EntanglementDistribution
from event_simulator import EventSimulator
from network_request import RequestGenerator


@dataclass(frozen=True)
class AlgorithmConfig:
    label: str
    source_method: str
    routing_method: str


DEFAULT_ALGORITHMS = (
    AlgorithmConfig("NOP-singlepath_star", "NOP", "singlepath_star"),
    AlgorithmConfig("NOP-multipath_tree_packing", "NOP", "multipath_tree_packing"),
    AlgorithmConfig("OP-singlepath_star", "OP", "singlepath_star"),
    AlgorithmConfig("OP-multipath_tree_packing", "OP", "multipath_tree_packing"),
)


def mean_and_ci95(samples: Iterable[float]) -> tuple:
    values = [float(x) for x in samples]
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    if n == 1:
        return mean, 0.0
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    half_width = 1.96 * (variance ** 0.5) / (n ** 0.5)
    return mean, half_width


def generate_request_batches(
    all_nodes: List[Any],
    num_trials: int,
    num_requests_per_trial: int,
    num_users_per_request: int,
    seed: Optional[int] = None,
) -> List[List[List[Any]]]:
    """
    Generate fixed multi-request batches for fair algorithm comparison.

    This intentionally uses RequestGenerator.random_users(), the same request
    generator used by run_simulator_3.py. Each algorithm should consume the
    returned request_batches instead of generating its own requests.
    """
    if num_trials < 1:
        raise ValueError("num_trials must be >= 1.")
    if num_requests_per_trial < 1:
        raise ValueError("num_requests_per_trial must be >= 1.")

    state = random.getstate()
    try:
        if seed is not None:
            random.seed(seed)

        generator = RequestGenerator(all_nodes)
        batches = []
        for _ in range(num_trials):
            trial_requests = [
                generator.random_users(k=num_users_per_request)
                for _ in range(num_requests_per_trial)
            ]
            batches.append(trial_requests)
    finally:
        random.setstate(state)

    return batches


def run_original_algorithm_on_request_batch(
    edge_list: List[tuple],
    request_batch: List[List[Any]],
    algorithm: AlgorithmConfig,
    p_op: float,
    cost_budget: int,
    max_per_edge: int,
    decoherence_time: int,
    max_timeslot: int,
    seed: Optional[int] = None,
    quiet: bool = True,
) -> Dict[str, Any]:
    """
    Run one original algorithm on one multi-request trial.

    The original EventSimulator.run_trials() is reused directly. Inside this
    adapter, each request in request_batch is passed as one original trial, and
    the multi-request trial throughput is aggregated as:

        sum(num_ghz / time_to_success) over requests in the batch.

    Failed requests contribute zero.
    """
    simulator = EventSimulator(
        edge_list=edge_list,
        num_users=len(request_batch[0]) if request_batch else 0,
        p_op=p_op,
        max_per_edge=max_per_edge,
        decoherence_time=decoherence_time,
        max_timeslot=max_timeslot,
    )
    dr_object = EntanglementDistribution()

    stream = open(os.devnull, "w", encoding="utf-8") if quiet else None
    try:
        cm = contextlib.redirect_stdout(stream) if quiet else contextlib.nullcontext()
        with cm:
            deployed_dicts = simulator.run_trials(
                user_sets=request_batch,
                routing_method=algorithm.routing_method,
                source_method=algorithm.source_method,
                seed=seed,
                dr_object=dr_object,
                cost_budget=cost_budget,
            )
    finally:
        if stream is not None:
            stream.close()

    summary = dr_object.get_summary_dict()
    request_dr_values = [
        num_ghz / time_to_success
        for time_to_success, num_ghz in dr_object.successful_trials
    ]
    trial_throughput = sum(request_dr_values)

    return {
        "algorithm": algorithm.label,
        "request_batch": request_batch,
        "num_requests": len(request_batch),
        "served_requests": summary["successful_runs"],
        "failed_requests": summary["failed_runs"],
        "trial_throughput": trial_throughput,
        "average_request_dr": summary["average_dr"],
        "request_dr_values": request_dr_values,
        "successful_times": summary["successful_times"],
        "successful_ghz_counts": summary["successful_ghz_counts"],
        "average_cost": summary["average_cost"],
        "deployed_dicts": deployed_dicts,
    }


def evaluate_original_algorithms_on_batches(
    edge_list: List[tuple],
    request_batches: List[List[List[Any]]],
    algorithms: Iterable[AlgorithmConfig] = DEFAULT_ALGORITHMS,
    p_op: float = base.OP_PROTOCOLS_1,
    cost_budget: int = base.FIXED_BUDGET,
    max_per_edge: int = base.MAX_PER_EDGE,
    decoherence_time: int = base.DECOHERENCE_TIME,
    max_timeslot: int = base.MAX_TIMEESLOT_PER_TRIAL,
    seed: int = base.RANDOM_SEED,
    quiet: bool = True,
) -> pd.DataFrame:
    """
    Evaluate algorithms on identical multi-request trial batches.
    """
    rows = []
    for algorithm in algorithms:
        trial_scores = []
        for trial_idx, request_batch in enumerate(request_batches):
            result = run_original_algorithm_on_request_batch(
                edge_list=edge_list,
                request_batch=request_batch,
                algorithm=algorithm,
                p_op=p_op,
                cost_budget=cost_budget,
                max_per_edge=max_per_edge,
                decoherence_time=decoherence_time,
                max_timeslot=max_timeslot,
                seed=seed + trial_idx,
                quiet=quiet,
            )
            trial_scores.append(result["trial_throughput"])
            rows.append(
                {
                    "algorithm": algorithm.label,
                    "trial": trial_idx,
                    "request_batch": str(request_batch),
                    "num_requests": result["num_requests"],
                    "served_requests": result["served_requests"],
                    "failed_requests": result["failed_requests"],
                    "trial_throughput": result["trial_throughput"],
                    "average_request_dr": result["average_request_dr"],
                    "successful_times": str(result["successful_times"]),
                    "successful_ghz_counts": str(result["successful_ghz_counts"]),
                    "average_cost": result["average_cost"],
                    "deployed_dicts": str(result["deployed_dicts"]),
                }
            )

        mean, ci95 = mean_and_ci95(trial_scores)
        rows.append(
            {
                "algorithm": algorithm.label,
                "trial": "SUMMARY",
                "request_batch": "",
                "num_requests": "",
                "served_requests": "",
                "failed_requests": "",
                "trial_throughput": mean,
                "average_request_dr": "",
                "successful_times": "",
                "successful_ghz_counts": "",
                "average_cost": "",
                "deployed_dicts": "",
                "ci95_halfwidth": ci95,
            }
        )

    return pd.DataFrame(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run original algorithms on fixed multi-request trial batches."
    )
    parser.add_argument("--num-trials", type=int, default=2)
    parser.add_argument("--num-requests", type=int, default=2)
    parser.add_argument("--num-users", type=int, default=base.NUM_USERS_PROTOCOLS_1)
    parser.add_argument("--budget", type=int, default=base.FIXED_BUDGET)
    parser.add_argument("--p-op", type=float, default=base.OP_PROTOCOLS_1)
    parser.add_argument("--max-timeslot", type=int, default=base.MAX_TIMEESLOT_PER_TRIAL)
    parser.add_argument("--seed", type=int, default=base.RANDOM_SEED)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    all_nodes = base.nodes_from_edge_list(base.EDGE_LIST)
    request_batches = generate_request_batches(
        all_nodes=all_nodes,
        num_trials=args.num_trials,
        num_requests_per_trial=args.num_requests,
        num_users_per_request=args.num_users,
        seed=args.seed,
    )

    df = evaluate_original_algorithms_on_batches(
        edge_list=base.EDGE_LIST,
        request_batches=request_batches,
        p_op=args.p_op,
        cost_budget=args.budget,
        max_timeslot=args.max_timeslot,
        seed=args.seed,
        quiet=not args.verbose,
    )

    summary = df[df["trial"] == "SUMMARY"][
        ["algorithm", "trial_throughput", "ci95_halfwidth"]
    ]
    print(summary.to_string(index=False))

    if args.output:
        df.to_csv(args.output, index=False)
        print(f"Saved detailed results to {args.output}")


if __name__ == "__main__":
    main()
