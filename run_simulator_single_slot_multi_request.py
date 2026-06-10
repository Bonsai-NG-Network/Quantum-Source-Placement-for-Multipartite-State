"""
Single-slot multi-request throughput simulator.

This entry point is for the journal-style objective:

    throughput = number of successfully established GHZ states per slot

Each trial is one time slot containing a batch of multipartite requests. Source
placement and elementary entanglement generation are performed once per trial.
Requests are then routed in a fixed order on the same QuantumNetwork instance,
so successful and failed operations consume the shared links/memory available in
that slot.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import random
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

import run_simulator_3 as base
from entanglement_link import EntanglementLink
from event_simulator import EventSimulator
from multipath_routing import MPGreedyRouting, MPCooperativeRouting, MPPackingRouting
from network_request import RequestGenerator
from quantum_source_placement import SourcePlacement
from quantum_source_placement_backup import SourcePlacementBackup
from quantum_source_placement_dp import SourcePlacementDP
from seed_utils import derive_seed, set_global_seed
from singlepath_routing import SPEntanglementRouting


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

ILP_ALGORITHMS = (
    AlgorithmConfig("ILP-singlepath_star", "ILP", "singlepath_star"),
    AlgorithmConfig("ILP-multipath_tree_packing", "ILP", "multipath_tree_packing"),
    AlgorithmConfig("LP_ROUND-singlepath_star", "LP_ROUND", "singlepath_star"),
    AlgorithmConfig("LP_ROUND-multipath_tree_packing", "LP_ROUND", "multipath_tree_packing"),
    AlgorithmConfig("ILP_CG-singlepath_star", "ILP_CG", "singlepath_star"),
    AlgorithmConfig("ILP_CG-multipath_tree_packing", "ILP_CG", "multipath_tree_packing"),
)

NO_DECOHERENCE_TIME = 10**12
SINGLE_SLOT_TIME = 1


def mean_and_ci95(samples: Iterable[float]) -> Tuple[float, float]:
    values = [float(x) for x in samples]
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    if n == 1:
        return mean, 0.0
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    return mean, 1.96 * (variance ** 0.5) / (n ** 0.5)


def ordered_unique(values: Sequence[Any]) -> List[Any]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def flatten_request_users(request_batch: Sequence[Sequence[Any]]) -> List[Any]:
    return ordered_unique([user for request in request_batch for user in request])


def generate_request_batches(
    all_nodes: List[Any],
    num_trials: int,
    num_requests_per_trial: int,
    num_users_per_request: int,
    seed: Optional[int] = None,
) -> List[List[List[Any]]]:
    if num_trials < 1:
        raise ValueError("num_trials must be >= 1.")
    if num_requests_per_trial < 1:
        raise ValueError("num_requests_per_trial must be >= 1.")
    if num_users_per_request < 2:
        raise ValueError("num_users_per_request must be >= 2.")

    state = random.getstate()
    try:
        set_global_seed(seed)
        generator = RequestGenerator(all_nodes)
        return [
            [
                generator.random_users(k=num_users_per_request)
                for _ in range(num_requests_per_trial)
            ]
            for _ in range(num_trials)
        ]
    finally:
        random.setstate(state)


def order_requests(
    request_batch: Sequence[Sequence[Any]],
    order: str,
    seed: Optional[int] = None,
) -> List[List[Any]]:
    requests = [list(req) for req in request_batch]
    if order == "given":
        return requests
    if order == "random":
        rng = random.Random(seed)
        rng.shuffle(requests)
        return requests
    raise ValueError(f"Unknown request order: {order}")


def place_sources_for_batch(
    simulator: EventSimulator,
    edge_list: List[tuple],
    request_batch: Sequence[Sequence[Any]],
    source_method: str,
    cost_budget: int,
    seed: Optional[int] = None,
    ilp_k_trees: int = 8,
    ilp_time_limit: Optional[float] = 60.0,
    ilp_mip_gap: Optional[float] = 0.01,
) -> Tuple[List[Tuple[Any, Any]], int, Dict[str, Any]]:
    set_global_seed(seed)
    batch_users = flatten_request_users(request_batch)
    if not batch_users:
        return [], 0, {}

    if source_method == "NOP":
        placer = SourcePlacement(simulator.topo)
        sources = placer.place_sources_for_request(
            batch_users,
            method="NOP",
            cost_budget=cost_budget,
            max_per_edge=simulator.max_per_edge,
        )
        return sources, placer.compute_cost(), {}

    if source_method == "OP":
        placer = SourcePlacementDP(simulator.topo)
        sources, _ = placer.place_sources_for_request(
            user_set=batch_users,
            cost_budget=cost_budget,
            max_per_edge=simulator.max_per_edge,
            K_steiner=4,
            k_paths=2,
            weight_attr="length_km",
            w_topo=0.25,
            w_demand=0.35,
            w_quality=0.4,
            w_overlap=0.0,
            p_map=None,
            p_op=simulator.p_op,
            value_model="prob",
        )
        return sources, placer.compute_cost(), {}

    if source_method == "OP_BP":
        placer = SourcePlacementBackup(simulator.topo)
        sources = placer.place_sources_for_request(
            user_set=batch_users,
            method="mt_overlap",
            cost_budget=cost_budget,
            max_per_edge=simulator.max_per_edge,
            k_trees=3,
            p_op=simulator.p_op,
            loss_coef_dB_per_km=0.2,
            seed=seed,
        )
        return sources, placer.compute_cost(), {}

    if source_method == "ILP":
        from ilp_multipartite_source_placement import solve_single_slot_ilp_request_batch

        ilp_result = solve_single_slot_ilp_request_batch(
            edge_list=edge_list,
            request_batch=request_batch,
            source_budget=cost_budget,
            max_sources_per_edge=simulator.max_per_edge,
            k_trees_per_request=ilp_k_trees,
            p_op=simulator.p_op,
            master_seed=seed,
            time_limit=ilp_time_limit,
            mip_gap=ilp_mip_gap,
            verbose=False,
        )
        source_placement = ilp_result.get("routing_source_placement") or ilp_result.get("source_placement", {})
        sources = []
        for edge, count in sorted(source_placement.items()):
            for _ in range(int(count)):
                sources.append(edge)

        metadata = {
            "ilp_objective": ilp_result.get("objective"),
            "ilp_status": ilp_result.get("status_name"),
            "ilp_candidate_seed": ilp_result.get("candidate_seed"),
            "ilp_solver_seed": ilp_result.get("solver_seed"),
            "ilp_selected_trees": ilp_result.get("throughput_selected_trees"),
            "ilp_candidate_tree_counts": ilp_result.get("candidate_tree_counts"),
        }
        return sources, len(sources), metadata

    if source_method == "LP_ROUND":
        from ilp_multipartite_source_placement_lp_rounding import solve_single_slot_lp_rounding_request_batch

        lp_result = solve_single_slot_lp_rounding_request_batch(
            edge_list=edge_list,
            request_batch=request_batch,
            source_budget=cost_budget,
            max_sources_per_edge=simulator.max_per_edge,
            k_trees_per_request=ilp_k_trees,
            p_op=simulator.p_op,
            master_seed=seed,
            verbose=False,
        )
        source_placement = lp_result.get("routing_source_placement") or lp_result.get("source_placement", {})
        sources = []
        for edge, count in sorted(source_placement.items()):
            for _ in range(int(count)):
                sources.append(edge)

        metadata = {
            "lp_objective": lp_result.get("lp_objective"),
            "lp_status": lp_result.get("status_name"),
            "lp_candidate_seed": lp_result.get("candidate_seed"),
            "lp_solver_seed": lp_result.get("solver_seed"),
            "lp_selected_trees": lp_result.get("throughput_selected_trees"),
            "lp_candidate_tree_counts": lp_result.get("candidate_tree_counts"),
        }
        return sources, len(sources), metadata

    if source_method == "ILP_CG":
        from ilp_multipartite_source_placement_cg import solve_single_slot_ilp_cg_request_batch

        ilp_result = solve_single_slot_ilp_cg_request_batch(
            edge_list=edge_list,
            request_batch=request_batch,
            source_budget=cost_budget,
            max_sources_per_edge=simulator.max_per_edge,
            k_initial_trees=max(1, min(2, ilp_k_trees)),
            pricing_trials=max(1, ilp_k_trees),
            max_iterations=20,
            p_op=simulator.p_op,
            master_seed=seed,
            final_time_limit=ilp_time_limit,
            final_mip_gap=ilp_mip_gap,
            verbose=False,
        )
        source_placement = ilp_result.get("routing_source_placement") or ilp_result.get("source_placement", {})
        sources = []
        for edge, count in sorted(source_placement.items()):
            for _ in range(int(count)):
                sources.append(edge)

        metadata = {
            "ilp_objective": ilp_result.get("objective"),
            "ilp_status": ilp_result.get("status_name"),
            "ilp_cg_iterations": ilp_result.get("cg_iterations"),
            "ilp_cg_added_columns": ilp_result.get("cg_added_columns"),
            "ilp_cg_initial_seed": ilp_result.get("cg_initial_seed"),
            "ilp_cg_final_solver_seed": ilp_result.get("cg_final_solver_seed"),
            "ilp_selected_trees": ilp_result.get("throughput_selected_trees"),
            "ilp_candidate_tree_counts": ilp_result.get("candidate_tree_counts"),
        }
        return sources, len(sources), metadata

    raise ValueError(f"Unknown source_method: {source_method}")


def deploy_elementary_links_once(
    simulator: EventSimulator,
    sources: Sequence[Tuple[Any, Any]],
    current_time: int = SINGLE_SLOT_TIME,
) -> Tuple[Dict[Tuple[Any, Any], int], Dict[Tuple[Any, Any], float]]:
    source_edge_list = [tuple(sorted(edge[:2])) for edge in sources]
    deployed_dict = dict(Counter(source_edge_list))

    edge_probs = {}
    for u, v in source_edge_list:
        length_km = simulator.topo.graph[u][v].get("length", 1)
        temp_link = EntanglementLink(
            link_id=f"{u}-{v}",
            nodes=[u, v],
            gen_time=current_time,
            length_km=length_km,
            p_op=simulator.p_op,
            loss_coef_dB_per_km=0.2,
        )
        edge_probs[(u, v)] = temp_link.p_e

    for u, v in source_edge_list:
        simulator.network.attempt_entanglement(
            u,
            v,
            p_op=simulator.p_op,
            gen_time=current_time,
        )

    return deployed_dict, edge_probs


def run_request_once_in_current_slot(
    simulator: EventSimulator,
    user_set: Sequence[Any],
    routing_method: str,
    edge_probs: Dict[Tuple[Any, Any], float],
    deployed_dict: Dict[Tuple[Any, Any], int],
) -> int:
    method_key = routing_method.lower()
    user_set = list(user_set)
    no_new_links = {}

    if method_key in {"pr", "singlepath_star"}:
        vc, _ = simulator.select_center_node(user_set, edge_probs, deployed_dict)
        if vc is None:
            return 0
        paths = simulator.get_shortest_paths_SP(vc, user_set)
        routing = SPEntanglementRouting(simulator.network, user_set, simulator.p_op)
        time_to_success, num_ghz = routing.singlepath_star_routing(
            vc,
            paths,
            max_timeslot=2,
            deployed_sources=no_new_links,
        )
        return num_ghz if time_to_success else 0

    if method_key == "singlepath_tree":
        routing = SPEntanglementRouting(simulator.network, user_set, simulator.p_op)
        time_to_success, num_ghz = routing.singlepath_tree_routing(
            max_timeslot=2,
            deployed_sources=no_new_links,
        )
        return num_ghz if time_to_success else 0

    if method_key in {"mpg", "multipath_star"}:
        vc, _ = simulator.select_center_node(user_set, edge_probs, deployed_dict)
        if vc is None:
            return 0
        routing = MPGreedyRouting(simulator.network, user_set, simulator.p_op)
        time_to_success = routing.multipath_star_routing(
            vc,
            max_timeslot=2,
            deployed_sources=no_new_links,
        )
        return 1 if time_to_success else 0

    if method_key in {"mpc", "multipath_tree"}:
        routing = MPCooperativeRouting(simulator.network, user_set, simulator.p_op)
        time_to_success = routing.multipath_tree_routing(
            max_timeslot=1,
            deployed_sources=no_new_links,
        )
        return 1 if time_to_success else 0

    if method_key in {"rr", "mpp", "multipath_tree_packing"}:
        routing = MPPackingRouting(simulator.network, user_set, simulator.p_op)
        time_to_success, num_ghz = routing.multipath_tree_packing_routing(
            max_timeslot=1,
            deployed_sources=no_new_links,
        )
        return num_ghz if time_to_success else 0

    raise ValueError(f"Unknown routing_method: {routing_method}")


def run_algorithm_on_single_slot_batch(
    edge_list: List[tuple],
    request_batch: Sequence[Sequence[Any]],
    algorithm: AlgorithmConfig,
    p_op: float,
    cost_budget: int,
    max_per_edge: int,
    decoherence_time: int,
    seed: Optional[int] = None,
    source_seed: Optional[int] = None,
    link_seed: Optional[int] = None,
    operation_seed_base: Optional[int] = None,
    request_order: str = "given",
    ilp_k_trees: int = 8,
    ilp_time_limit: Optional[float] = 60.0,
    ilp_mip_gap: Optional[float] = 0.01,
    quiet: bool = True,
) -> Dict[str, Any]:
    if source_seed is None:
        source_seed = derive_seed(seed, "source-placement")
    if link_seed is None:
        link_seed = derive_seed(seed, "elementary-links")
    if operation_seed_base is None:
        operation_seed_base = derive_seed(seed, "request-operations")

    simulator = EventSimulator(
        edge_list=edge_list,
        num_users=len(request_batch[0]) if request_batch else 0,
        p_op=p_op,
        max_per_edge=max_per_edge,
        decoherence_time=decoherence_time,
        max_timeslot=1,
    )

    stream = open(os.devnull, "w", encoding="utf-8") if quiet else None
    try:
        cm = contextlib.redirect_stdout(stream) if quiet else contextlib.nullcontext()
        with cm:
            sources, cost, source_metadata = place_sources_for_batch(
                simulator=simulator,
                edge_list=edge_list,
                request_batch=request_batch,
                source_method=algorithm.source_method,
                cost_budget=cost_budget,
                seed=source_seed,
                ilp_k_trees=ilp_k_trees,
                ilp_time_limit=ilp_time_limit,
                ilp_mip_gap=ilp_mip_gap,
            )
            set_global_seed(link_seed)
            deployed_dict, edge_probs = deploy_elementary_links_once(
                simulator,
                sources,
                current_time=SINGLE_SLOT_TIME,
            )
            ordered_requests = order_requests(request_batch, request_order, seed=seed)

            per_request_ghz = []
            request_operation_seeds = []
            for request_idx, request in enumerate(ordered_requests):
                operation_seed = derive_seed(operation_seed_base, "request-operation", request_idx)
                request_operation_seeds.append(operation_seed)
                set_global_seed(operation_seed)
                ghz_count = run_request_once_in_current_slot(
                    simulator=simulator,
                    user_set=request,
                    routing_method=algorithm.routing_method,
                    edge_probs=edge_probs,
                    deployed_dict=deployed_dict,
                )
                per_request_ghz.append(ghz_count)
    finally:
        if stream is not None:
            stream.close()

    throughput = sum(per_request_ghz)
    return {
        "algorithm": algorithm.label,
        "request_batch": [list(req) for req in request_batch],
        "request_order": request_order,
        "ordered_requests": ordered_requests,
        "throughput_qbps": throughput,
        "served_requests": sum(1 for count in per_request_ghz if count > 0),
        "failed_requests": sum(1 for count in per_request_ghz if count == 0),
        "per_request_ghz": per_request_ghz,
        "cost": cost,
        "deployed_dict": deployed_dict,
        "seed": seed,
        "source_seed": source_seed,
        "link_seed": link_seed,
        "operation_seed_base": operation_seed_base,
        "request_operation_seeds": request_operation_seeds,
        "source_metadata": source_metadata,
    }


def evaluate_algorithms(
    edge_list: List[tuple],
    request_batches: Sequence[Sequence[Sequence[Any]]],
    algorithms: Iterable[AlgorithmConfig] = DEFAULT_ALGORITHMS,
    p_op: float = base.OP_PROTOCOLS_1,
    cost_budget: int = base.FIXED_BUDGET,
    max_per_edge: int = base.MAX_PER_EDGE,
    decoherence_time: int = NO_DECOHERENCE_TIME,
    seed: int = base.RANDOM_SEED,
    request_order: str = "given",
    ilp_k_trees: int = 8,
    ilp_time_limit: Optional[float] = 60.0,
    ilp_mip_gap: Optional[float] = 0.01,
    quiet: bool = True,
) -> pd.DataFrame:
    rows = []
    for algorithm in algorithms:
        trial_scores = []
        for trial_idx, request_batch in enumerate(request_batches):
            request_order_seed = derive_seed(seed, "request-order", "trial", trial_idx)
            ordered_request_batch = order_requests(
                request_batch,
                request_order,
                seed=request_order_seed,
            )
            run_seed = derive_seed(seed, "trial", trial_idx, "algorithm", algorithm.label)
            source_seed = derive_seed(seed, "trial", trial_idx, "source", algorithm.source_method)
            link_seed = derive_seed(seed, "trial", trial_idx, "source", algorithm.source_method, "elementary-links")
            operation_seed_base = derive_seed(seed, "trial", trial_idx, "algorithm", algorithm.label, "operations")
            result = run_algorithm_on_single_slot_batch(
                edge_list=edge_list,
                request_batch=ordered_request_batch,
                algorithm=algorithm,
                p_op=p_op,
                cost_budget=cost_budget,
                max_per_edge=max_per_edge,
                decoherence_time=decoherence_time,
                seed=run_seed,
                source_seed=source_seed,
                link_seed=link_seed,
                operation_seed_base=operation_seed_base,
                request_order="given",
                ilp_k_trees=ilp_k_trees,
                ilp_time_limit=ilp_time_limit,
                ilp_mip_gap=ilp_mip_gap,
                quiet=quiet,
            )
            trial_scores.append(result["throughput_qbps"])
            rows.append(
                {
                    "algorithm": algorithm.label,
                    "trial": trial_idx,
                    "throughput_qbps": result["throughput_qbps"],
                    "served_requests": result["served_requests"],
                    "failed_requests": result["failed_requests"],
                    "per_request_ghz": str(result["per_request_ghz"]),
                    "request_batch": str(result["request_batch"]),
                    "ordered_requests": str(result["ordered_requests"]),
                    "cost": result["cost"],
                    "deployed_dict": str(result["deployed_dict"]),
                    "seed": result["seed"],
                    "source_seed": result["source_seed"],
                    "link_seed": result["link_seed"],
                    "operation_seed_base": result["operation_seed_base"],
                    "request_operation_seeds": str(result["request_operation_seeds"]),
                    "master_seed": seed,
                    "request_order_seed": request_order_seed,
                    "source_metadata": str(result["source_metadata"]),
                }
            )

        mean, ci95 = mean_and_ci95(trial_scores)
        rows.append(
            {
                "algorithm": algorithm.label,
                "trial": "SUMMARY",
                "throughput_qbps": mean,
                "served_requests": "",
                "failed_requests": "",
                "per_request_ghz": "",
                "request_batch": "",
                "ordered_requests": "",
                "cost": "",
                "deployed_dict": "",
                "seed": "",
                "source_seed": "",
                "link_seed": "",
                "operation_seed_base": "",
                "request_operation_seeds": "",
                "master_seed": seed,
                "request_order_seed": "",
                "source_metadata": "",
                "ci95_halfwidth": ci95,
            }
        )

    return pd.DataFrame(rows)


def build_algorithm_configs(include_ilp: bool = False) -> Tuple[AlgorithmConfig, ...]:
    if include_ilp:
        return DEFAULT_ALGORITHMS + ILP_ALGORITHMS
    return DEFAULT_ALGORITHMS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one-slot multi-request throughput experiments."
    )
    parser.add_argument("--num-trials", type=int, default=2)
    parser.add_argument("--num-requests", type=int, default=2)
    parser.add_argument("--num-users", type=int, default=base.NUM_USERS_PROTOCOLS_1)
    parser.add_argument("--budget", type=int, default=base.FIXED_BUDGET)
    parser.add_argument("--p-op", type=float, default=base.OP_PROTOCOLS_1)
    parser.add_argument("--decoherence-time", type=int, default=NO_DECOHERENCE_TIME)
    parser.add_argument("--seed", type=int, default=base.RANDOM_SEED)
    parser.add_argument("--request-order", choices=["given", "random"], default="given")
    parser.add_argument("--include-ilp", action="store_true")
    parser.add_argument("--ilp-k-trees", type=int, default=8)
    parser.add_argument("--ilp-time-limit", type=float, default=60.0)
    parser.add_argument("--ilp-mip-gap", type=float, default=0.01)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    set_global_seed(args.seed)
    all_nodes = base.nodes_from_edge_list(base.EDGE_LIST)
    request_seed = derive_seed(args.seed, "requests")
    request_batches = generate_request_batches(
        all_nodes=all_nodes,
        num_trials=args.num_trials,
        num_requests_per_trial=args.num_requests,
        num_users_per_request=args.num_users,
        seed=request_seed,
    )

    df = evaluate_algorithms(
        edge_list=base.EDGE_LIST,
        request_batches=request_batches,
        algorithms=build_algorithm_configs(include_ilp=args.include_ilp),
        p_op=args.p_op,
        cost_budget=args.budget,
        max_per_edge=base.MAX_PER_EDGE,
        decoherence_time=args.decoherence_time,
        seed=args.seed,
        request_order=args.request_order,
        ilp_k_trees=args.ilp_k_trees,
        ilp_time_limit=args.ilp_time_limit,
        ilp_mip_gap=args.ilp_mip_gap,
        quiet=not args.verbose,
    )

    summary = df[df["trial"] == "SUMMARY"][
        ["algorithm", "throughput_qbps", "ci95_halfwidth"]
    ]
    print(summary.to_string(index=False))

    if args.output:
        df.to_csv(args.output, index=False)
        print(f"Saved detailed results to {args.output}")


if __name__ == "__main__":
    main()
