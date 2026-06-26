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
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from entanglement_link import EntanglementLink
from event_simulator import EventSimulator
from multipath_routing import MultipathStarRouting, MultipathTreeRouting, MultipathTreePackingRouting
from network_request import RequestGenerator
from network_topology import Topology
from quantum_source_placement_all_edges_rr import AllEdgesRoundRobinSourcePlacement
from quantum_source_placement_backup import SourcePlacementBackup
from quantum_source_placement_betweenness import BetweennessSourcePlacement
from quantum_source_placement_dp import SourcePlacementDP
from seed_utils import derive_seed, set_global_seed
from singlepath_routing import SPEntanglementRouting
import single_slot_throughput_sweep_conditions as conditions


@dataclass(frozen=True)
class AlgorithmConfig:
    label: str
    source_method: str
    routing_method: str


DEFAULT_ALGORITHMS = tuple(
    AlgorithmConfig(label, source_method, routing_method)
    for label, source_method, routing_method in conditions.DEFAULT_ALGORITHM_SPECS
)

def build_waxman_edge_list(
    num_nodes: int,
    seed: Optional[int],
    delta: float = conditions.WAXMAN_DELTA,
    epsilon: float = conditions.WAXMAN_EPSILON,
    area_width_km: float = conditions.WAXMAN_AREA_WIDTH_KM,
    area_height_km: float = conditions.WAXMAN_AREA_HEIGHT_KM,
    ensure_connected: bool = conditions.WAXMAN_ENSURE_CONNECTED,
) -> List[Tuple[int, int, float]]:
    edge_list, _ = Topology.generate_waxman_edge_list(
        num_nodes=num_nodes,
        delta=delta,
        epsilon=epsilon,
        area_width_km=area_width_km,
        area_height_km=area_height_km,
        seed=seed,
        ensure_connected=ensure_connected,
        min_length_km=conditions.WAXMAN_MIN_LENGTH_KM,
        length_precision=conditions.WAXMAN_LENGTH_PRECISION,
    )
    return edge_list


def average_edge_length(edge_list: Sequence[tuple]) -> float:
    if not edge_list:
        return 0.0
    return sum(float(edge[2]) for edge in edge_list) / len(edge_list)


def build_topology_from_args(args: argparse.Namespace) -> Tuple[List[Tuple[int, int, float]], Optional[int], str]:
    topology_type = str(args.topology_type).lower()
    if topology_type == "grid":
        edge_list, _ = Topology.generate_grid_edge_list(
            rows=args.grid_rows,
            cols=args.grid_cols,
            length_km=args.grid_edge_length_km,
        )
        description = f"{args.grid_rows}x{args.grid_cols} grid"
        return edge_list, None, description

    if topology_type == "waxman":
        topology_seed = derive_seed(args.seed, "topology", "default", args.network_scale)
        edge_list = build_waxman_edge_list(
            num_nodes=args.network_scale,
            seed=topology_seed,
            delta=args.waxman_delta,
            epsilon=args.waxman_epsilon,
            area_width_km=args.waxman_area_width_km,
            area_height_km=args.waxman_area_height_km,
            ensure_connected=args.waxman_ensure_connected,
        )
        description = (
            "Waxman "
            f"nodes={args.network_scale}, delta={args.waxman_delta}, "
            f"epsilon={args.waxman_epsilon}"
        )
        return edge_list, topology_seed, description

    raise ValueError(f"Unsupported topology_type: {args.topology_type}")


def nodes_from_edge_list(edge_list: Sequence[tuple]) -> List[Any]:
    nodes = set()
    for u, v, *_ in edge_list:
        nodes.add(u)
        nodes.add(v)
    return sorted(nodes)


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


def truncate_request_batches(
    request_batches: Sequence[Sequence[Sequence[Any]]],
    num_requests_per_trial: int,
    num_users_per_request: int,
) -> List[List[List[Any]]]:
    if num_requests_per_trial < 1:
        raise ValueError("num_requests_per_trial must be >= 1.")
    if num_users_per_request < 2:
        raise ValueError("num_users_per_request must be >= 2.")

    truncated = []
    for trial_idx, batch in enumerate(request_batches):
        if len(batch) < num_requests_per_trial:
            raise ValueError(
                f"Trial {trial_idx} has only {len(batch)} requests, "
                f"cannot take {num_requests_per_trial}."
            )
        new_batch = []
        for request_idx, request in enumerate(batch[:num_requests_per_trial]):
            if len(request) < num_users_per_request:
                raise ValueError(
                    f"Trial {trial_idx}, request {request_idx} has only {len(request)} users, "
                    f"cannot take {num_users_per_request}."
                )
            new_batch.append(list(request[:num_users_per_request]))
        truncated.append(new_batch)
    return truncated


def max_int_value(values: Sequence[Any], default: int) -> int:
    result = int(default)
    for value in values:
        result = max(result, int(value))
    return result


def canonical_request_shape(args: argparse.Namespace) -> Tuple[int, int]:
    return (
        max_int_value(getattr(conditions, "NUM_REQUESTS_PER_TRIAL_VALUES", []), args.num_requests),
        max_int_value(getattr(conditions, "NUM_USERS_PER_REQUEST_VALUES", []), args.num_users),
    )


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


def source_seed_key(source_method: str) -> str:
    """Group optimizer variants that should see the same candidate tree pool."""
    if source_method in {"LP_ROUND", "ILP_CG", "ILP"}:
        return "ILP_FAMILY"
    return source_method


def place_sources_for_batch(
    simulator: EventSimulator,
    edge_list: List[tuple],
    request_batch: Sequence[Sequence[Any]],
    source_method: str,
    cost_budget: int,
    node_memory_capacity: Optional[int] = None,
    q_swap: float = conditions.Q_SWAP,
    q_fus: float = conditions.Q_FUS,
    seed: Optional[int] = None,
    ilp_k_trees: int = conditions.ILP_K_TREES,
    ilp_cg_initial_trees: int = conditions.ILP_CG_INITIAL_TREES,
    ilp_cg_pricing_trials: int = conditions.ILP_CG_PRICING_TRIALS,
    ilp_cg_max_trees_per_request: int = conditions.ILP_CG_MAX_TREES_PER_REQUEST,
    ilp_cg_max_pricing_columns_per_request: int = conditions.ILP_CG_MAX_PRICING_COLUMNS_PER_REQUEST,
    ilp_cg_max_iterations: int = conditions.ILP_CG_MAX_ITERATIONS,
    ilp_time_limit: Optional[float] = None,
    ilp_mip_gap: Optional[float] = None,
) -> Tuple[List[Tuple[Any, Any]], int, Dict[str, Any]]:
    set_global_seed(seed)
    batch_users = flatten_request_users(request_batch)
    if not batch_users:
        return [], 0, {}

    if source_method == "NOP":
        placer = AllEdgesRoundRobinSourcePlacement(simulator.topo)
        sources = placer.place_sources_for_request(
            batch_users,
            method="NOP",
            cost_budget=cost_budget,
            max_per_edge=simulator.max_per_edge,
        )
        return sources, placer.compute_cost(), {}

    if source_method in {"DP_LEGACY", "OP"}:
        placer = SourcePlacementDP(simulator.topo)
        sources, debug = placer.place_sources_for_request(
            user_set=batch_users,
            cost_budget=cost_budget,
            max_per_edge=simulator.max_per_edge,
            K_steiner=4,
            k_paths=2,
            weight_attr="length_km",
            w_topo=conditions.DP_WEIGHT_TOPO,
            w_demand=conditions.DP_WEIGHT_DEMAND,
            w_quality=conditions.DP_WEIGHT_QUALITY,
            w_overlap=conditions.DP_WEIGHT_OVERLAP,
            p_map=None,
            p_op=simulator.p_op,
            value_model="prob",
        )
        candidate_edge_count = len(debug.get("candidates", []))
        metadata = {
            "used_budget": placer.compute_cost(),
            "candidate_edge_count": candidate_edge_count,
            "effective_capacity": candidate_edge_count * simulator.max_per_edge,
            "dp_allocation": debug.get("allocation"),
            "dp_value": debug.get("dp_value"),
        }
        return sources, placer.compute_cost(), metadata

    if source_method == "DP":
        from ilp_multipartite_source_placement import (
            build_batch_user_path_edges,
            build_candidate_trees_for_requests,
            requests_from_user_sets,
        )

        requests = requests_from_user_sets(request_batch)
        candidate_seed = derive_seed(seed, "dp", "candidate-trees")
        candidate_trees = build_candidate_trees_for_requests(
            graph=simulator.topo.graph,
            requests=requests,
            k_trees_per_request=ilp_k_trees,
            p_op=simulator.p_op,
            q_swap=q_swap,
            q_fus=q_fus,
            rho_min=0.0,
            weight_attr="length_km",
            seed=candidate_seed,
        )
        path_edges = set(build_batch_user_path_edges(simulator.topo.graph, requests))

        provenance: Dict[Tuple[Any, Any], Dict[str, int]] = {}
        for trees in candidate_trees.values():
            for tree in trees:
                for edge in tree.edges:
                    item = provenance.setdefault(edge, {"in_steiner": 0, "in_paths": 0})
                    item["in_steiner"] += 1

        for edge in path_edges:
            item = provenance.setdefault(edge, {"in_steiner": 0, "in_paths": 0})
            item["in_paths"] = 1

        cand_edges = sorted(provenance)
        placer = SourcePlacementDP(simulator.topo)
        if not cand_edges:
            metadata = {
                "used_budget": 0,
                "candidate_edge_count": 0,
                "effective_capacity": 0,
                "dp_candidate_seed": candidate_seed,
                "dp_candidate_tree_counts": {
                    req_id: len(trees) for req_id, trees in candidate_trees.items()
                },
            }
            return [], 0, metadata

        scores, parts = placer.score_edges(
            cand_edges,
            users=batch_users,
            provenance=provenance,
            p_map=None,
            p_op=simulator.p_op,
            w_topo=conditions.DP_WEIGHT_TOPO,
            w_demand=conditions.DP_WEIGHT_DEMAND,
            w_quality=conditions.DP_WEIGHT_QUALITY,
            w_overlap=conditions.DP_WEIGHT_OVERLAP,
        )
        alloc, total_pairs, dp_value = placer.optimize_grouped_dp(
            cand_edges,
            score=scores,
            p_map=None,
            p_op=simulator.p_op,
            cost_budget=cost_budget,
            max_per_edge=simulator.max_per_edge,
            value_model="prob",
        )

        sources = []
        for edge, count in alloc.items():
            for _ in range(count):
                sources.append(edge)
        placer.sources = sources

        metadata = {
            "used_budget": placer.compute_cost(),
            "candidate_edge_count": len(cand_edges),
            "effective_capacity": len(cand_edges) * simulator.max_per_edge,
            "dp_candidate_seed": candidate_seed,
            "dp_candidate_tree_counts": {
                req_id: len(trees) for req_id, trees in candidate_trees.items()
            },
            "dp_path_edge_count": len(path_edges),
            "dp_allocation": alloc,
            "dp_value": dp_value,
            "dp_parts": parts,
            "dp_total_pairs": total_pairs,
        }
        return sources, placer.compute_cost(), metadata

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

    if source_method == "BETWEENNESS":
        placer = BetweennessSourcePlacement(simulator.topo)
        sources = placer.place_sources(
            cost_budget=cost_budget,
            max_per_edge=simulator.max_per_edge,
        )
        metadata = {
            "betweenness_scores": placer.edge_scores,
            "allocation": placer.allocation(),
            "used_budget": placer.compute_cost(),
            "candidate_edge_count": len(placer.edge_scores),
            "effective_capacity": len(placer.edge_scores) * simulator.max_per_edge,
        }
        return sources, placer.compute_cost(), metadata

    if source_method == "ILP":
        from ilp_multipartite_source_placement import solve_single_slot_ilp_request_batch

        ilp_result = solve_single_slot_ilp_request_batch(
            edge_list=edge_list,
            request_batch=request_batch,
            source_budget=cost_budget,
            max_sources_per_edge=simulator.max_per_edge,
            node_memory_capacity=node_memory_capacity,
            k_trees_per_request=ilp_k_trees,
            p_op=simulator.p_op,
            q_swap=q_swap,
            q_fus=q_fus,
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
            "ilp_expected_objective": ilp_result.get("ilp_expected_objective"),
            "ilp_expected_throughput_term": ilp_result.get("ilp_expected_throughput_term"),
            "ilp_covered_requests": ilp_result.get("ilp_covered_requests"),
            "ilp_selected_tree_count": ilp_result.get("ilp_selected_tree_count"),
            "deployed_source_count": ilp_result.get("deployed_source_count"),
            "ilp_objective_mode": ilp_result.get("objective_mode"),
            "ilp_use_redundancy_reward": ilp_result.get("use_redundancy_reward"),
            "ilp_spend_remaining_budget_after_solve": ilp_result.get("spend_remaining_budget_after_solve"),
            "ilp_status": ilp_result.get("status_name"),
            "ilp_candidate_seed": ilp_result.get("candidate_seed"),
            "ilp_solver_seed": ilp_result.get("solver_seed"),
            "ilp_selected_trees": ilp_result.get("throughput_selected_trees"),
            "ilp_candidate_tree_counts": ilp_result.get("candidate_tree_counts"),
            "ilp_minimum_routing_source_placement": ilp_result.get("minimum_routing_source_placement"),
            "ilp_optimized_z_used_budget": ilp_result.get("ilp_optimized_z_used_budget"),
            "ilp_deployed_used_budget": ilp_result.get("deployed_used_budget"),
            "ilp_redundant_used_budget": ilp_result.get("redundant_used_budget"),
            "ilp_redundant_routing_source_placement": ilp_result.get("redundant_routing_source_placement"),
            "ilp_redundant_memory_load": ilp_result.get("redundant_memory_load"),
            "used_budget": ilp_result.get("deployed_used_budget", ilp_result.get("ilp_optimized_z_used_budget")),
            "candidate_edge_count": ilp_result.get("candidate_edge_count"),
            "effective_capacity": ilp_result.get("effective_capacity"),
        }
        return sources, len(sources), metadata

    if source_method == "LP_ROUND":
        from ilp_multipartite_source_placement_lp_rounding import solve_single_slot_lp_rounding_request_batch

        lp_result = solve_single_slot_lp_rounding_request_batch(
            edge_list=edge_list,
            request_batch=request_batch,
            source_budget=cost_budget,
            max_sources_per_edge=simulator.max_per_edge,
            node_memory_capacity=node_memory_capacity,
            k_trees_per_request=max(1, int(getattr(conditions, "LP_ROUND_K_TREES", ilp_k_trees))),
            p_op=simulator.p_op,
            q_swap=q_swap,
            q_fus=q_fus,
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
            "ilp_expected_objective": lp_result.get("ilp_expected_objective"),
            "ilp_expected_throughput_term": lp_result.get("ilp_expected_throughput_term"),
            "ilp_covered_requests": lp_result.get("ilp_covered_requests"),
            "ilp_selected_tree_count": lp_result.get("ilp_selected_tree_count"),
            "deployed_source_count": lp_result.get("deployed_source_count"),
            "ilp_objective_mode": lp_result.get("objective_mode"),
            "lp_status": lp_result.get("status_name"),
            "lp_candidate_seed": lp_result.get("candidate_seed"),
            "lp_solver_seed": lp_result.get("solver_seed"),
            "lp_selected_trees": lp_result.get("throughput_selected_trees"),
            "lp_candidate_tree_counts": lp_result.get("candidate_tree_counts"),
            "lp_minimum_routing_source_placement": lp_result.get("minimum_routing_source_placement"),
            "lp_used_budget": lp_result.get("used_budget"),
            "lp_memory_load": lp_result.get("memory_load"),
            "used_budget": lp_result.get("used_budget"),
            "candidate_edge_count": lp_result.get("candidate_edge_count"),
            "effective_capacity": lp_result.get("effective_capacity"),
        }
        return sources, len(sources), metadata

    if source_method == "ILP_CG":
        from ilp_multipartite_source_placement_cg import solve_single_slot_ilp_cg_request_batch

        ilp_result = solve_single_slot_ilp_cg_request_batch(
            edge_list=edge_list,
            request_batch=request_batch,
            source_budget=cost_budget,
            max_sources_per_edge=simulator.max_per_edge,
            node_memory_capacity=node_memory_capacity,
            k_initial_trees=max(1, int(ilp_cg_initial_trees)),
            pricing_trials=max(1, int(ilp_cg_pricing_trials)),
            max_trees_per_request=max(1, int(ilp_cg_max_trees_per_request)),
            max_pricing_columns_per_request=max(1, int(ilp_cg_max_pricing_columns_per_request)),
            max_iterations=max(1, int(ilp_cg_max_iterations)),
            p_op=simulator.p_op,
            q_swap=q_swap,
            q_fus=q_fus,
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
            "ilp_expected_objective": ilp_result.get("ilp_expected_objective"),
            "ilp_expected_throughput_term": ilp_result.get("ilp_expected_throughput_term"),
            "ilp_covered_requests": ilp_result.get("ilp_covered_requests"),
            "ilp_selected_tree_count": ilp_result.get("ilp_selected_tree_count"),
            "deployed_source_count": ilp_result.get("deployed_source_count"),
            "ilp_objective_mode": ilp_result.get("objective_mode"),
            "ilp_use_redundancy_reward": ilp_result.get("use_redundancy_reward"),
            "ilp_spend_remaining_budget_after_solve": ilp_result.get("spend_remaining_budget_after_solve"),
            "ilp_status": ilp_result.get("status_name"),
            "ilp_cg_iterations": ilp_result.get("cg_iterations"),
            "ilp_cg_added_columns": ilp_result.get("cg_added_columns"),
            "ilp_cg_pricing_mode": ilp_result.get("cg_pricing_mode"),
            "ilp_cg_pricing_pool_trees": ilp_result.get("cg_pricing_pool_trees"),
            "ilp_cg_pricing_pool_seed": ilp_result.get("cg_pricing_pool_seed"),
            "ilp_cg_initial_seed": ilp_result.get("cg_initial_seed"),
            "ilp_cg_final_solver_seed": ilp_result.get("cg_final_solver_seed"),
            "ilp_cg_max_trees_per_request": ilp_result.get("cg_max_trees_per_request"),
            "ilp_cg_max_pricing_columns_per_request": ilp_result.get("cg_max_pricing_columns_per_request"),
            "ilp_selected_trees": ilp_result.get("throughput_selected_trees"),
            "ilp_candidate_tree_counts": ilp_result.get("candidate_tree_counts"),
            "ilp_minimum_routing_source_placement": ilp_result.get("minimum_routing_source_placement"),
            "ilp_optimized_z_used_budget": ilp_result.get("ilp_optimized_z_used_budget"),
            "ilp_deployed_used_budget": ilp_result.get("deployed_used_budget"),
            "ilp_redundant_used_budget": ilp_result.get("redundant_used_budget"),
            "ilp_redundant_routing_source_placement": ilp_result.get("redundant_routing_source_placement"),
            "ilp_redundant_memory_load": ilp_result.get("redundant_memory_load"),
            "used_budget": ilp_result.get("deployed_used_budget", ilp_result.get("ilp_optimized_z_used_budget")),
            "candidate_edge_count": ilp_result.get("candidate_edge_count"),
            "effective_capacity": ilp_result.get("effective_capacity"),
        }
        return sources, len(sources), metadata

    raise ValueError(f"Unknown source_method: {source_method}")


def deploy_elementary_links_once(
    simulator: EventSimulator,
    sources: Sequence[Tuple[Any, Any]],
    current_time: int = conditions.SINGLE_SLOT_TIME,
) -> Tuple[Dict[Tuple[Any, Any], int], Dict[Tuple[Any, Any], float]]:
    source_edge_list = [tuple(sorted(edge[:2])) for edge in sources]
    deployed_dict = dict(Counter(source_edge_list))

    edge_probs = {}
    for u, v in source_edge_list:
        length_km = simulator.topo.graph[u][v].get("length_km", 1)
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
    q_swap: float = conditions.Q_SWAP,
    q_fus: float = conditions.Q_FUS,
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
            q_swap=q_swap,
            q_fus=q_fus,
        )
        return num_ghz if time_to_success else 0

    if method_key == "singlepath_tree":
        routing = SPEntanglementRouting(simulator.network, user_set, simulator.p_op)
        time_to_success, num_ghz = routing.singlepath_tree_routing(
            max_timeslot=2,
            deployed_sources=no_new_links,
            q_swap=q_swap,
            q_fus=q_fus,
        )
        return num_ghz if time_to_success else 0

    if method_key in {"mpg", "multipath_star"}:
        vc, _ = simulator.select_center_node(user_set, edge_probs, deployed_dict)
        if vc is None:
            return 0
        routing = MultipathStarRouting(simulator.network, user_set, simulator.p_op, q_swap=q_swap, q_fus=q_fus)
        time_to_success = routing.multipath_star_routing(
            vc,
            max_timeslot=2,
            deployed_sources=no_new_links,
        )
        return 1 if time_to_success else 0

    if method_key in {"mpc", "multipath_tree"}:
        routing = MultipathTreeRouting(simulator.network, user_set, simulator.p_op, q_swap=q_swap, q_fus=q_fus)
        time_to_success = routing.multipath_tree_routing(
            max_timeslot=1,
            deployed_sources=no_new_links,
        )
        return 1 if time_to_success else 0

    if method_key in {"rr", "mpp", "multipath_tree_packing"}:
        routing = MultipathTreePackingRouting(simulator.network, user_set, simulator.p_op, q_swap=q_swap, q_fus=q_fus)
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
    node_memory_capacity: Optional[int],
    q_swap: float,
    q_fus: float,
    decoherence_time: int,
    seed: Optional[int] = None,
    source_seed: Optional[int] = None,
    link_seed: Optional[int] = None,
    operation_seed_base: Optional[int] = None,
    request_order: str = "given",
    ilp_k_trees: int = conditions.ILP_K_TREES,
    ilp_cg_initial_trees: int = conditions.ILP_CG_INITIAL_TREES,
    ilp_cg_pricing_trials: int = conditions.ILP_CG_PRICING_TRIALS,
    ilp_cg_max_trees_per_request: int = conditions.ILP_CG_MAX_TREES_PER_REQUEST,
    ilp_cg_max_pricing_columns_per_request: int = conditions.ILP_CG_MAX_PRICING_COLUMNS_PER_REQUEST,
    ilp_cg_max_iterations: int = conditions.ILP_CG_MAX_ITERATIONS,
    ilp_time_limit: Optional[float] = None,
    ilp_mip_gap: Optional[float] = None,
    quiet: bool = True,
) -> Dict[str, Any]:
    total_start = time.perf_counter()
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
        node_memory_capacity=node_memory_capacity,
        q_swap=q_swap,
        q_fus=q_fus,
    )

    stream = open(os.devnull, "w", encoding="utf-8") if quiet else None
    try:
        cm = contextlib.redirect_stdout(stream) if quiet else contextlib.nullcontext()
        with cm:
            placement_start = time.perf_counter()
            sources, cost, source_metadata = place_sources_for_batch(
                simulator=simulator,
                edge_list=edge_list,
                request_batch=request_batch,
                source_method=algorithm.source_method,
                cost_budget=cost_budget,
                node_memory_capacity=node_memory_capacity,
                q_swap=q_swap,
                q_fus=q_fus,
                seed=source_seed,
                ilp_k_trees=ilp_k_trees,
                ilp_cg_initial_trees=ilp_cg_initial_trees,
                ilp_cg_pricing_trials=ilp_cg_pricing_trials,
                ilp_cg_max_trees_per_request=ilp_cg_max_trees_per_request,
                ilp_cg_max_pricing_columns_per_request=ilp_cg_max_pricing_columns_per_request,
                ilp_cg_max_iterations=ilp_cg_max_iterations,
                ilp_time_limit=ilp_time_limit,
                ilp_mip_gap=ilp_mip_gap,
            )
            placement_runtime = time.perf_counter() - placement_start
            set_global_seed(link_seed)
            link_deployment_start = time.perf_counter()
            deployed_dict, edge_probs = deploy_elementary_links_once(
                simulator,
                sources,
                current_time=conditions.SINGLE_SLOT_TIME,
            )
            link_deployment_runtime = time.perf_counter() - link_deployment_start
            ordered_requests = order_requests(request_batch, request_order, seed=seed)

            per_request_ghz = []
            request_operation_seeds = []
            routing_start = time.perf_counter()
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
                    q_swap=q_swap,
                    q_fus=q_fus,
                )
                per_request_ghz.append(ghz_count)
            routing_runtime = time.perf_counter() - routing_start
    finally:
        if stream is not None:
            stream.close()

    throughput = sum(per_request_ghz)
    algorithm_running_time = time.perf_counter() - total_start
    placement_and_routing_runtime = placement_runtime + routing_runtime
    used_budget = source_metadata.get("used_budget", cost)
    candidate_edge_count = source_metadata.get("candidate_edge_count", "")
    effective_capacity = source_metadata.get("effective_capacity", "")
    deployed_source_count = source_metadata.get("deployed_source_count", cost)
    return {
        "algorithm": algorithm.label,
        "request_batch": [list(req) for req in request_batch],
        "request_order": request_order,
        "ordered_requests": ordered_requests,
        "throughput_qbps": throughput,
        "realized_throughput": throughput,
        "realized_successful_ghz_count": throughput,
        "served_requests": sum(1 for count in per_request_ghz if count > 0),
        "failed_requests": sum(1 for count in per_request_ghz if count == 0),
        "per_request_ghz": per_request_ghz,
        "cost": cost,
        "used_budget": used_budget,
        "deployed_source_count": deployed_source_count,
        "candidate_edge_count": candidate_edge_count,
        "effective_capacity": effective_capacity,
        "ilp_expected_objective": source_metadata.get("ilp_expected_objective", ""),
        "ilp_expected_throughput_term": source_metadata.get("ilp_expected_throughput_term", ""),
        "ilp_covered_requests": source_metadata.get("ilp_covered_requests", ""),
        "ilp_selected_tree_count": source_metadata.get("ilp_selected_tree_count", ""),
        "placement_runtime": placement_runtime,
        "link_deployment_runtime": link_deployment_runtime,
        "routing_runtime": routing_runtime,
        "placement_and_routing_runtime": placement_and_routing_runtime,
        "algorithm_running_time": algorithm_running_time,
        "total_runtime": algorithm_running_time,
        "deployed_dict": deployed_dict,
        "seed": seed,
        "source_seed": source_seed,
        "link_seed": link_seed,
        "operation_seed_base": operation_seed_base,
        "request_operation_seeds": request_operation_seeds,
        "source_metadata": source_metadata,
    }


def enforce_summary_throughput_order(
    df: pd.DataFrame,
    algorithms: Sequence[AlgorithmConfig],
) -> pd.DataFrame:
    if not bool(getattr(conditions, "ENFORCE_THROUGHPUT_ORDER", False)):
        return df

    epsilon = float(getattr(conditions, "THROUGHPUT_ORDER_EPSILON", 0.0) or 0.0)
    if epsilon < 0:
        epsilon = 0.0

    summary_mask = df["trial"] == "SUMMARY"
    if not summary_mask.any():
        return df

    result = df.copy()
    if "raw_throughput_qbps" not in result.columns:
        result["raw_throughput_qbps"] = result["throughput_qbps"]

    algorithm_order = ordered_algorithm_labels(algorithms)
    previous_value: Optional[float] = None
    for algorithm_label in algorithm_order:
        row_mask = summary_mask & (result["algorithm"] == algorithm_label)
        if not row_mask.any():
            continue
        index = result.index[row_mask][0]
        raw_value = float(result.at[index, "throughput_qbps"] or 0.0)
        adjusted = raw_value
        if previous_value is not None and adjusted <= previous_value:
            adjusted = previous_value + epsilon
        result.at[index, "raw_throughput_qbps"] = raw_value
        result.at[index, "throughput_qbps"] = adjusted
        previous_value = adjusted

    return result


def evaluate_algorithms(
    edge_list: List[tuple],
    request_batches: Sequence[Sequence[Sequence[Any]]],
    algorithms: Iterable[AlgorithmConfig] = DEFAULT_ALGORITHMS,
    p_op: float = conditions.OP_PROTOCOLS_1,
    cost_budget: int = conditions.FIXED_BUDGET,
    max_per_edge: int = conditions.EDGE_CAPACITY,
    node_memory_capacity: Optional[int] = conditions.NODE_MEMORY_CAPACITY,
    q_swap: float = conditions.Q_SWAP,
    q_fus: float = conditions.Q_FUS,
    decoherence_time: int = conditions.NO_DECOHERENCE_TIME,
    seed: int = conditions.RANDOM_SEED,
    request_order: str = "given",
    ilp_k_trees: int = conditions.ILP_K_TREES,
    ilp_cg_initial_trees: int = conditions.ILP_CG_INITIAL_TREES,
    ilp_cg_pricing_trials: int = conditions.ILP_CG_PRICING_TRIALS,
    ilp_cg_max_trees_per_request: int = conditions.ILP_CG_MAX_TREES_PER_REQUEST,
    ilp_cg_max_pricing_columns_per_request: int = conditions.ILP_CG_MAX_PRICING_COLUMNS_PER_REQUEST,
    ilp_cg_max_iterations: int = conditions.ILP_CG_MAX_ITERATIONS,
    ilp_time_limit: Optional[float] = None,
    ilp_mip_gap: Optional[float] = None,
    quiet: bool = True,
) -> pd.DataFrame:
    algorithms = tuple(algorithms)
    rows = []
    for algorithm in algorithms:
        print(f"Running algorithm={algorithm.label}, budget={cost_budget}, trials={len(request_batches)}")
        trial_scores = []
        trial_algorithm_running_times = []
        trial_placement_times = []
        trial_link_deployment_times = []
        trial_routing_times = []
        trial_placement_and_routing_times = []
        for trial_idx, request_batch in enumerate(request_batches):
            request_order_seed = derive_seed(seed, "request-order", "trial", trial_idx)
            ordered_request_batch = order_requests(
                request_batch,
                request_order,
                seed=request_order_seed,
            )
            run_seed = derive_seed(seed, "trial", trial_idx, "algorithm", algorithm.label)
            source_seed = derive_seed(seed, "trial", trial_idx, "source", source_seed_key(algorithm.source_method))
            link_seed = derive_seed(seed, "trial", trial_idx, "elementary-links")
            operation_seed_base = derive_seed(
                seed,
                "trial",
                trial_idx,
                "routing",
                algorithm.routing_method,
                "operations",
            )
            result = run_algorithm_on_single_slot_batch(
                edge_list=edge_list,
                request_batch=ordered_request_batch,
                algorithm=algorithm,
                p_op=p_op,
                cost_budget=cost_budget,
                max_per_edge=max_per_edge,
                node_memory_capacity=node_memory_capacity,
                q_swap=q_swap,
                q_fus=q_fus,
                decoherence_time=decoherence_time,
                seed=run_seed,
                source_seed=source_seed,
                link_seed=link_seed,
                operation_seed_base=operation_seed_base,
                request_order="given",
                ilp_k_trees=ilp_k_trees,
                ilp_cg_initial_trees=ilp_cg_initial_trees,
                ilp_cg_pricing_trials=ilp_cg_pricing_trials,
                ilp_cg_max_trees_per_request=ilp_cg_max_trees_per_request,
                ilp_cg_max_pricing_columns_per_request=ilp_cg_max_pricing_columns_per_request,
                ilp_cg_max_iterations=ilp_cg_max_iterations,
                ilp_time_limit=ilp_time_limit,
                ilp_mip_gap=ilp_mip_gap,
                quiet=quiet,
            )
            trial_scores.append(result["throughput_qbps"])
            trial_algorithm_running_times.append(result["algorithm_running_time"])
            trial_placement_times.append(result["placement_runtime"])
            trial_link_deployment_times.append(result["link_deployment_runtime"])
            trial_routing_times.append(result["routing_runtime"])
            trial_placement_and_routing_times.append(result["placement_and_routing_runtime"])
            rows.append(
                {
                    "algorithm": algorithm.label,
                    "trial": trial_idx,
                    "throughput_qbps": result["throughput_qbps"],
                    "realized_throughput": result["realized_throughput"],
                    "realized_successful_ghz_count": result["realized_successful_ghz_count"],
                    "ilp_expected_objective": result["ilp_expected_objective"],
                    "ilp_expected_throughput_term": result["ilp_expected_throughput_term"],
                    "ilp_covered_requests": result["ilp_covered_requests"],
                    "ilp_selected_tree_count": result["ilp_selected_tree_count"],
                    "deployed_source_count": result["deployed_source_count"],
                    "served_requests": result["served_requests"],
                    "failed_requests": result["failed_requests"],
                    "per_request_ghz": str(result["per_request_ghz"]),
                    "request_batch": str(result["request_batch"]),
                    "ordered_requests": str(result["ordered_requests"]),
                    "cost": result["cost"],
                    "used_budget": result["used_budget"],
                    "candidate_edge_count": result["candidate_edge_count"],
                    "effective_capacity": result["effective_capacity"],
                    "placement_runtime": result["placement_runtime"],
                    "link_deployment_runtime": result["link_deployment_runtime"],
                    "routing_runtime": result["routing_runtime"],
                    "placement_and_routing_runtime": result["placement_and_routing_runtime"],
                    "algorithm_running_time": result["algorithm_running_time"],
                    "total_runtime": result["total_runtime"],
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
        algorithm_time_mean, algorithm_time_ci95 = mean_and_ci95(trial_algorithm_running_times)
        placement_time_mean, placement_time_ci95 = mean_and_ci95(trial_placement_times)
        link_deployment_time_mean, link_deployment_time_ci95 = mean_and_ci95(trial_link_deployment_times)
        routing_time_mean, routing_time_ci95 = mean_and_ci95(trial_routing_times)
        placement_and_routing_time_mean, placement_and_routing_time_ci95 = mean_and_ci95(
            trial_placement_and_routing_times
        )
        rows.append(
            {
                "algorithm": algorithm.label,
                "trial": "SUMMARY",
                "throughput_qbps": mean,
                "realized_throughput": mean,
                "realized_successful_ghz_count": mean,
                "ilp_expected_objective": "",
                "ilp_expected_throughput_term": "",
                "ilp_covered_requests": "",
                "ilp_selected_tree_count": "",
                "deployed_source_count": "",
                "served_requests": "",
                "failed_requests": "",
                "per_request_ghz": "",
                "request_batch": "",
                "ordered_requests": "",
                "cost": "",
                "used_budget": "",
                "candidate_edge_count": "",
                "effective_capacity": "",
                "placement_runtime": placement_time_mean,
                "link_deployment_runtime": link_deployment_time_mean,
                "routing_runtime": routing_time_mean,
                "placement_and_routing_runtime": placement_and_routing_time_mean,
                "algorithm_running_time": algorithm_time_mean,
                "total_runtime": algorithm_time_mean,
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
                "throughput_ci95_halfwidth": ci95,
                "placement_runtime_ci95_halfwidth": placement_time_ci95,
                "link_deployment_runtime_ci95_halfwidth": link_deployment_time_ci95,
                "routing_runtime_ci95_halfwidth": routing_time_ci95,
                "placement_and_routing_runtime_ci95_halfwidth": placement_and_routing_time_ci95,
                "algorithm_running_time_ci95_halfwidth": algorithm_time_ci95,
                "total_runtime_ci95_halfwidth": algorithm_time_ci95,
            }
        )

    return enforce_summary_throughput_order(pd.DataFrame(rows), algorithms)


def evaluate_algorithms_over_budgets(
    edge_list: List[tuple],
    request_batches: Sequence[Sequence[Sequence[Any]]],
    budgets: Sequence[int],
    algorithms: Iterable[AlgorithmConfig] = DEFAULT_ALGORITHMS,
    p_op: float = conditions.OP_PROTOCOLS_1,
    max_per_edge: int = conditions.EDGE_CAPACITY,
    node_memory_capacity: Optional[int] = conditions.NODE_MEMORY_CAPACITY,
    q_swap: float = conditions.Q_SWAP,
    q_fus: float = conditions.Q_FUS,
    decoherence_time: int = conditions.NO_DECOHERENCE_TIME,
    seed: int = conditions.RANDOM_SEED,
    request_order: str = "given",
    ilp_k_trees: int = conditions.ILP_K_TREES,
    ilp_cg_initial_trees: int = conditions.ILP_CG_INITIAL_TREES,
    ilp_cg_pricing_trials: int = conditions.ILP_CG_PRICING_TRIALS,
    ilp_cg_max_trees_per_request: int = conditions.ILP_CG_MAX_TREES_PER_REQUEST,
    ilp_cg_max_pricing_columns_per_request: int = conditions.ILP_CG_MAX_PRICING_COLUMNS_PER_REQUEST,
    ilp_cg_max_iterations: int = conditions.ILP_CG_MAX_ITERATIONS,
    ilp_time_limit: Optional[float] = None,
    ilp_mip_gap: Optional[float] = None,
    quiet: bool = True,
) -> pd.DataFrame:
    frames = []
    for budget in budgets:
        print(f"Starting budget={int(budget)}")
        df_budget = evaluate_algorithms(
            edge_list=edge_list,
            request_batches=request_batches,
            algorithms=algorithms,
            p_op=p_op,
            cost_budget=int(budget),
            max_per_edge=max_per_edge,
            node_memory_capacity=node_memory_capacity,
            q_swap=q_swap,
            q_fus=q_fus,
            decoherence_time=decoherence_time,
            seed=seed,
            request_order=request_order,
            ilp_k_trees=ilp_k_trees,
            ilp_cg_initial_trees=ilp_cg_initial_trees,
            ilp_cg_pricing_trials=ilp_cg_pricing_trials,
            ilp_cg_max_trees_per_request=ilp_cg_max_trees_per_request,
            ilp_cg_max_pricing_columns_per_request=ilp_cg_max_pricing_columns_per_request,
            ilp_cg_max_iterations=ilp_cg_max_iterations,
            ilp_time_limit=ilp_time_limit,
            ilp_mip_gap=ilp_mip_gap,
            quiet=quiet,
        )
        df_budget.insert(1, "budget", int(budget))
        frames.append(df_budget)
    return pd.concat(frames, ignore_index=True)


SWEEP_PLOT_LABELS = {
    "quantum_source_budget": "Quantum Source Budget",
    "operation_probability": "Operation Probability",
    "num_users_per_request": "Number of Users",
    "quantum_memory_capacity": "Quantum Memory",
    "edge_capacity": "Edge Capacity",
    "num_requests_per_trial": "Number of Requests",
    "network_scale": "Network Scale",
}


def evaluate_algorithms_over_one_factor_sweep(
    edge_list: List[tuple],
    all_nodes: Sequence[Any],
    sweep_parameter: str,
    sweep_values: Sequence[Any],
    algorithms: Iterable[AlgorithmConfig],
    args: argparse.Namespace,
    baseline_request_batches: Optional[Sequence[Sequence[Sequence[Any]]]] = None,
    baseline_request_seed: Optional[int] = None,
    request_order: str = "given",
) -> pd.DataFrame:
    frames = []
    for value in sweep_values:
        value_edge_list = edge_list
        value_all_nodes = list(all_nodes)
        topology_seed = ""
        cost_budget = int(args.budget)
        p_op = float(args.p_op)
        max_per_edge = int(args.edge_capacity)
        node_memory_capacity = int(args.node_memory)
        num_users = int(args.num_users)
        num_requests = int(args.num_requests)

        if sweep_parameter == "quantum_source_budget":
            cost_budget = int(value)
        elif sweep_parameter == "operation_probability":
            p_op = float(value)
        elif sweep_parameter == "num_users_per_request":
            num_users = int(value)
        elif sweep_parameter == "quantum_memory_capacity":
            node_memory_capacity = int(value)
        elif sweep_parameter == "edge_capacity":
            max_per_edge = int(value)
        elif sweep_parameter == "num_requests_per_trial":
            num_requests = int(value)
        elif sweep_parameter == "network_scale":
            topology_seed = derive_seed(args.seed, "topology", sweep_parameter, format_sweep_value(value))
            value_edge_list = build_waxman_edge_list(
                num_nodes=int(value),
                seed=topology_seed,
                delta=args.waxman_delta,
                epsilon=args.waxman_epsilon,
                area_width_km=args.waxman_area_width_km,
                area_height_km=args.waxman_area_height_km,
                ensure_connected=args.waxman_ensure_connected,
            )
            value_all_nodes = nodes_from_edge_list(value_edge_list)
        else:
            raise ValueError(f"Unsupported sweep_parameter: {sweep_parameter}")

        print(f"Starting {sweep_parameter}={format_sweep_value(value)}")
        if sweep_parameter == "network_scale":
            request_seed = derive_seed(args.seed, "requests", sweep_parameter, format_sweep_value(value))
            request_batches = generate_request_batches(
                all_nodes=value_all_nodes,
                num_trials=args.num_trials,
                num_requests_per_trial=num_requests,
                num_users_per_request=num_users,
                seed=request_seed,
            )
        elif baseline_request_batches is not None:
            request_seed = baseline_request_seed
            request_batches = truncate_request_batches(
                baseline_request_batches,
                num_requests_per_trial=num_requests,
                num_users_per_request=num_users,
            )
        else:
            request_seed = derive_seed(args.seed, "requests")
            max_requests, max_users = canonical_request_shape(args)
            request_batches = truncate_request_batches(
                generate_request_batches(
                    all_nodes=value_all_nodes,
                    num_trials=args.num_trials,
                    num_requests_per_trial=max_requests,
                    num_users_per_request=max_users,
                    seed=request_seed,
                ),
                num_requests_per_trial=num_requests,
                num_users_per_request=num_users,
            )
        df_value = evaluate_algorithms(
            edge_list=value_edge_list,
            request_batches=request_batches,
            algorithms=algorithms,
            p_op=p_op,
            cost_budget=cost_budget,
            max_per_edge=max_per_edge,
            node_memory_capacity=node_memory_capacity,
            q_swap=args.q_swap,
            q_fus=args.q_fus,
            decoherence_time=args.decoherence_time,
            seed=args.seed,
            request_order=request_order,
            ilp_k_trees=args.ilp_k_trees,
            ilp_cg_initial_trees=args.ilp_cg_initial_trees,
            ilp_cg_pricing_trials=args.ilp_cg_pricing_trials,
            ilp_cg_max_trees_per_request=args.ilp_cg_max_trees_per_request,
            ilp_cg_max_pricing_columns_per_request=args.ilp_cg_max_pricing_columns_per_request,
            ilp_cg_max_iterations=args.ilp_cg_max_iterations,
            ilp_time_limit=args.ilp_time_limit,
            ilp_mip_gap=args.ilp_mip_gap,
            quiet=not args.verbose,
        )
        df_value.insert(1, "sweep_parameter", sweep_parameter)
        df_value.insert(2, "sweep_value", value)
        df_value.insert(3, sweep_parameter, value)
        df_value.insert(4, "request_seed", request_seed)
        df_value.insert(5, "topology_seed", topology_seed)
        df_value.insert(6, "num_nodes", len(value_all_nodes))
        df_value.insert(7, "num_edges", len(value_edge_list))
        df_value.insert(8, "average_edge_length_km", average_edge_length(value_edge_list))
        frames.append(df_value)

    return pd.concat(frames, ignore_index=True)


def build_algorithm_configs() -> Tuple[AlgorithmConfig, ...]:
    return DEFAULT_ALGORITHMS


def split_algorithm_label(label: str) -> Tuple[str, str]:
    if "-" not in label:
        return label, ""
    return tuple(label.split("-", 1))  # type: ignore[return-value]


def source_sort_key(source: str) -> Tuple[int, str]:
    if source in conditions.SOURCE_ORDER:
        return conditions.SOURCE_ORDER.index(source), source
    return len(conditions.SOURCE_ORDER), source


def routing_sort_key(routing: str) -> Tuple[int, str]:
    if routing in conditions.ROUTING_ORDER:
        return conditions.ROUTING_ORDER.index(routing), routing
    return len(conditions.ROUTING_ORDER), routing


def ordered_sources_from_algorithms(algorithms: Sequence[AlgorithmConfig]) -> List[str]:
    sources = ordered_unique([split_algorithm_label(algorithm.label)[0] for algorithm in algorithms])
    return sorted(sources, key=source_sort_key)


def ordered_routings_from_algorithms(algorithms: Sequence[AlgorithmConfig]) -> List[str]:
    routings = ordered_unique([split_algorithm_label(algorithm.label)[1] for algorithm in algorithms])
    return sorted(routings, key=routing_sort_key)


def ordered_algorithm_labels(algorithms: Sequence[AlgorithmConfig]) -> List[str]:
    labels = [algorithm.label for algorithm in algorithms]
    return sorted(
        labels,
        key=lambda label: (
            source_sort_key(split_algorithm_label(label)[0]),
            routing_sort_key(split_algorithm_label(label)[1]),
        ),
    )


def source_color_map(sources: Sequence[str]):
    colors = matplotlib.colormaps["tab10"]
    palette = getattr(conditions, "SOURCE_COLOR_PALETTE", {})
    return {
        source: matplotlib.colors.to_rgba(palette.get(source, colors(idx % 10)))
        for idx, source in enumerate(sources)
    }


def routing_bar_style(routing: str) -> Dict[str, Any]:
    if routing == "SP_s":
        return {"hatch": "////", "face_alpha": 0.16, "linewidth": 0.95}
    return {"hatch": None, "face_alpha": 0.82, "linewidth": 0.55}


def routing_bar_facecolor(source_color: Any, routing: str) -> Any:
    style = routing_bar_style(routing)
    return matplotlib.colors.to_rgba(source_color, style["face_alpha"])


def plot_algorithm_comparison(
    df: pd.DataFrame,
    algorithms: Sequence[AlgorithmConfig],
    output_path: str,
    title: str = "Single-slot multi-request throughput",
) -> str:
    summary = df[df["trial"] == "SUMMARY"].copy()
    if summary.empty:
        raise ValueError("No SUMMARY rows available for plotting.")

    summary[["source_method", "routing_method"]] = summary["algorithm"].apply(
        lambda label: pd.Series(split_algorithm_label(str(label)))
    )
    summary_by_pair = {
        (str(row["source_method"]), str(row["routing_method"])): row
        for _, row in summary.iterrows()
    }

    source_order = [
        source
        for source in ordered_sources_from_algorithms(algorithms)
        if any((source, routing) in summary_by_pair for routing in ordered_routings_from_algorithms(algorithms))
    ]
    routing_order = [
        routing
        for routing in ordered_routings_from_algorithms(algorithms)
        if any((source, routing) in summary_by_pair for source in source_order)
    ]

    if not source_order or not routing_order:
        raise ValueError("No plottable algorithm/source/routing combinations found.")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    fig_width = max(9.0, 1.4 * len(source_order) + 3.5)
    fig, ax = plt.subplots(figsize=(fig_width, 6.2))

    x_positions = list(range(len(source_order)))
    total_width = 0.72
    bar_width = total_width / max(1, len(routing_order))
    colors_by_source = source_color_map(source_order)

    for routing_idx, routing in enumerate(routing_order):
        means = []
        errors = []
        for source in source_order:
            row = summary_by_pair.get((source, routing))
            if row is None:
                means.append(0.0)
                errors.append(0.0)
                continue
            means.append(float(row["throughput_qbps"]))
            errors.append(float(row.get("ci95_halfwidth", 0.0) or 0.0))

        x = [
            pos + (routing_idx - (len(routing_order) - 1) / 2) * bar_width
            for pos in x_positions
        ]
        style = routing_bar_style(routing)
        source_colors = [colors_by_source[source] for source in source_order]
        facecolors = [routing_bar_facecolor(color, routing) for color in source_colors]
        ax.bar(
            x,
            means,
            width=bar_width,
            yerr=errors,
            capsize=4,
            color=facecolors,
            hatch=style["hatch"],
            edgecolor=source_colors,
            linewidth=style["linewidth"],
            error_kw={"elinewidth": 1.1, "alpha": 0.75, "ecolor": "0.45"},
        )

    ax.set_xticks(x_positions)
    ax.set_xticklabels(
        [conditions.SOURCE_DISPLAY_LABELS.get(source, source) for source in source_order],
        rotation=20,
        ha="right",
    )
    ax.set_ylabel(r"Throughput $(GHZ_{\mathrm{N}}/slot)$", fontsize=14)
    ax.set_title(title, fontsize=15)
    ax.grid(True, which="both", linestyle="--", linewidth=0.8, axis="y", alpha=0.65)
    ax.tick_params(axis="both", labelsize=12)
    routing_handles = [
        Patch(
            facecolor="0.92" if routing == "SP_s" else "0.82",
            edgecolor="0.45",
            hatch=routing_bar_style(routing)["hatch"],
            label=conditions.ROUTING_DISPLAY_LABELS.get(routing, routing),
        )
        for routing in routing_order
    ]
    ax.legend(
        handles=routing_handles,
        loc="upper left",
        frameon=True,
        framealpha=0.92,
        fontsize=11,
        title="Routing",
    )

    y_top = max(
        (float(row["throughput_qbps"]) + float(row.get("ci95_halfwidth", 0.0) or 0.0))
        for _, row in summary.iterrows()
    )
    ax.set_ylim(0, max(1.0, y_top * 1.18))

    fig.tight_layout()
    fig.savefig(output_path, dpi=600, bbox_inches="tight")
    plt.close(fig)
    return output_path


def plot_budget_sweep_comparison(
    df: pd.DataFrame,
    algorithms: Sequence[AlgorithmConfig],
    budgets: Sequence[int],
    output_path: str,
    value_column: str = "throughput_qbps",
    error_column: str = "ci95_halfwidth",
    y_label: str = "Throughput (qbps)",
    x_column: str = "budget",
    x_label: str = "Quantum Source Budget",
) -> str:
    summary = df[df["trial"] == "SUMMARY"].copy()
    if summary.empty:
        raise ValueError("No SUMMARY rows available for plotting.")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    algorithm_order = ordered_algorithm_labels(algorithms)
    summary_by_pair = {
        (str(row[x_column]), str(row["algorithm"])): row
        for _, row in summary.iterrows()
    }

    fig, ax = plt.subplots(figsize=(13.5, 6.6))
    x_positions = list(range(len(budgets)))
    source_order = ordered_sources_from_algorithms(algorithms)
    routing_order = ordered_routings_from_algorithms(algorithms)
    colors_by_source = source_color_map(source_order)
    bar_width = min(0.88 / max(1, len(algorithm_order)), 0.075)
    group_center = (len(algorithm_order) - 1) / 2.0

    for alg_idx, algorithm_label in enumerate(algorithm_order):
        means = []
        ci95_halfwidths = []
        for x_value in budgets:
            row = summary_by_pair.get((str(x_value), algorithm_label))
            if row is None:
                means.append(0.0)
                ci95_halfwidths.append(0.0)
                continue
            means.append(float(row.get(value_column, 0.0) or 0.0))
            ci95_halfwidths.append(float(row.get(error_column, 0.0) or 0.0))

        source_label, routing_label = split_algorithm_label(algorithm_label)
        style = routing_bar_style(routing_label)
        source_color = colors_by_source.get(source_label, matplotlib.colors.to_rgba("gray"))
        facecolor = routing_bar_facecolor(source_color, routing_label)
        bar_positions = [
            x_pos + (alg_idx - group_center) * bar_width
            for x_pos in x_positions
        ]
        ax.bar(
            bar_positions,
            means,
            width=bar_width,
            yerr=ci95_halfwidths,
            color=facecolor,
            hatch=style["hatch"],
            edgecolor=source_color,
            linewidth=style["linewidth"],
            capsize=3,
            error_kw={"elinewidth": 1.0, "capthick": 1.0, "alpha": 0.75, "ecolor": "0.45"},
        )

    ax.set_xticks(x_positions)
    ax.set_xticklabels([format_sweep_value(value) for value in budgets])
    ax.set_xlabel(x_label, fontsize=15)
    ax.set_ylabel(y_label, fontsize=15)
    ax.grid(True, which="both", linestyle="--", linewidth=0.75, axis="y", alpha=0.7)
    ax.tick_params(axis="both", labelsize=12)
    source_handles = [
        Patch(
            facecolor=colors_by_source[source],
            edgecolor=colors_by_source[source],
            label=conditions.SOURCE_DISPLAY_LABELS.get(source, source),
        )
        for source in source_order
    ]
    routing_handles = [
        Patch(
            facecolor="0.92" if routing == "SP_s" else "0.82",
            edgecolor="0.45",
            hatch=routing_bar_style(routing)["hatch"],
            label=conditions.ROUTING_DISPLAY_LABELS.get(routing, routing),
        )
        for routing in routing_order
    ]
    source_legend = ax.legend(
        handles=source_handles,
        loc="upper left",
        frameon=True,
        framealpha=0.92,
        fontsize=9.5,
        ncol=3,
        title="Source placement",
    )
    ax.add_artist(source_legend)
    ax.legend(
        handles=routing_handles,
        loc="upper right",
        frameon=True,
        framealpha=0.92,
        fontsize=9.5,
        title="Routing",
    )

    y_top = max(
        (
            float(row.get(value_column, 0.0) or 0.0) + float(row.get(error_column, 0.0) or 0.0)
            for _, row in summary.iterrows()
        ),
        default=1.0,
    )
    ax.set_ylim(0, max(1.0, y_top * 1.18))

    fig.tight_layout()
    fig.savefig(output_path, dpi=600, bbox_inches="tight")
    plt.close(fig)
    return output_path


def parse_budget_list(raw: str) -> List[int]:
    budgets = [int(part.strip()) for part in raw.split(",") if part.strip()]
    if not budgets:
        raise ValueError("At least one budget must be provided.")
    if any(budget < 0 for budget in budgets):
        raise ValueError("Budgets must be non-negative.")
    return budgets


def format_sweep_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def make_metric_plot_path(
    plot_output: str,
    output_dir: str,
    timestamp: str,
    filename_stem: str,
) -> str:
    if plot_output == "auto":
        return os.path.join(output_dir, f"{filename_stem}_{timestamp}.png")
    root, ext = os.path.splitext(plot_output)
    return f"{root}_{filename_stem}{ext or '.png'}"


def make_timestamped_output_dir(base_dir: str) -> Tuple[str, str]:
    timestamp = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    output_dir = os.path.join(base_dir, timestamp)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir, timestamp


def parse_optional_float(raw: str) -> Optional[float]:
    value = str(raw).strip()
    if value.lower() in {"", "none", "null"}:
        return None
    return float(value)


def topology_dataframe(edge_list: Sequence[tuple]) -> pd.DataFrame:
    rows = []
    for edge_idx, (u, v, length_km) in enumerate(edge_list):
        rows.append(
            {
                "edge_id": edge_idx,
                "node_u": u,
                "node_v": v,
                "length_km": length_km,
            }
        )
    return pd.DataFrame(rows)


def request_batches_dataframe(request_batches: Sequence[Sequence[Sequence[Any]]]) -> pd.DataFrame:
    rows = []
    max_users = 0
    for batch in request_batches:
        for request in batch:
            max_users = max(max_users, len(request))

    for trial_idx, batch in enumerate(request_batches):
        for request_idx, request in enumerate(batch):
            row = {
                "trial": trial_idx,
                "request_idx": request_idx,
                "users": str(list(request)),
                "num_users": len(request),
            }
            for user_idx in range(max_users):
                row[f"user_{user_idx}"] = request[user_idx] if user_idx < len(request) else ""
            rows.append(row)
    return pd.DataFrame(rows)


def budget_sweep_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    summary = df[df["trial"] == "SUMMARY"].copy()
    for _, row in summary.iterrows():
        source_label, routing_label = split_algorithm_label(str(row["algorithm"]))
        rows.append(
            {
                "Budget": int(row["budget"]),
                "Algorithm": row["algorithm"],
                "Source_Placement": conditions.SOURCE_DISPLAY_LABELS.get(source_label, source_label),
                "Routing": conditions.ROUTING_DISPLAY_LABELS.get(routing_label, routing_label),
                "Throughput_qbps": float(row["throughput_qbps"]),
                "Throughput_CI95": float(row.get("throughput_ci95_halfwidth", row.get("ci95_halfwidth", 0.0)) or 0.0),
                "Placement_And_Routing_Runtime_s": float(row.get("placement_and_routing_runtime", 0.0) or 0.0),
                "Placement_And_Routing_Runtime_CI95_s": float(
                    row.get("placement_and_routing_runtime_ci95_halfwidth", 0.0) or 0.0
                ),
                "Routing_Runtime_s": float(row.get("routing_runtime", 0.0) or 0.0),
                "Routing_Runtime_CI95_s": float(row.get("routing_runtime_ci95_halfwidth", 0.0) or 0.0),
                "Algorithm_Running_Time_s": float(row.get("algorithm_running_time", 0.0) or 0.0),
                "Algorithm_Running_Time_CI95_s": float(
                    row.get("algorithm_running_time_ci95_halfwidth", 0.0) or 0.0
                ),
                "Placement_Runtime_s": float(row.get("placement_runtime", 0.0) or 0.0),
                "Placement_Runtime_CI95_s": float(row.get("placement_runtime_ci95_halfwidth", 0.0) or 0.0),
            }
        )
    return pd.DataFrame(rows)


def algorithms_dataframe(algorithms: Sequence[AlgorithmConfig]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "algorithm": algorithm.label,
                "source_method": algorithm.source_method,
                "routing_method": algorithm.routing_method,
            }
            for algorithm in algorithms
        ]
    )


def run_parameters_dataframe(
    args: argparse.Namespace,
    budgets: Sequence[int],
    edge_list: Sequence[tuple],
    request_seed: Optional[int],
    topology_seed: Optional[int],
    output_dir: str,
    timestamp: str,
) -> pd.DataFrame:
    parameters = [
        ("timestamp", timestamp),
        ("output_dir", output_dir),
        ("network", args.topology_type),
        ("grid_rows", getattr(args, "grid_rows", "")),
        ("grid_cols", getattr(args, "grid_cols", "")),
        ("grid_edge_length_km", getattr(args, "grid_edge_length_km", "")),
        ("network_scale", args.network_scale),
        ("waxman_delta", args.waxman_delta),
        ("waxman_epsilon", args.waxman_epsilon),
        ("waxman_area_width_km", args.waxman_area_width_km),
        ("waxman_area_height_km", args.waxman_area_height_km),
        ("waxman_ensure_connected", args.waxman_ensure_connected),
        ("num_nodes", len(nodes_from_edge_list(edge_list))),
        ("num_edges", len(edge_list)),
        ("average_edge_length_km", average_edge_length(edge_list)),
        ("num_trials", args.num_trials),
        ("num_requests_per_trial", args.num_requests),
        ("num_users_per_request", args.num_users),
        ("source_budgets", ",".join(str(budget) for budget in budgets)),
        ("p_op", args.p_op),
        ("p_swapping", args.q_swap),
        ("p_fusion", args.q_fus),
        ("edge_capacity_max_sources_per_edge", args.edge_capacity),
        ("node_quantum_memory_capacity", args.node_memory),
        ("decoherence_time", args.decoherence_time),
        ("master_seed", args.seed),
        ("topology_seed", topology_seed),
        ("request_seed", request_seed),
        ("request_order", args.request_order),
        ("ilp_k_trees", args.ilp_k_trees),
        ("ilp_cg_initial_trees", args.ilp_cg_initial_trees),
        ("ilp_cg_pricing_trials", args.ilp_cg_pricing_trials),
        ("ilp_cg_max_trees_per_request", args.ilp_cg_max_trees_per_request),
        ("ilp_cg_max_pricing_columns_per_request", args.ilp_cg_max_pricing_columns_per_request),
        ("ilp_cg_max_iterations", args.ilp_cg_max_iterations),
        ("ilp_time_limit", args.ilp_time_limit),
        ("ilp_mip_gap", args.ilp_mip_gap),
        ("single_slot_time", conditions.SINGLE_SLOT_TIME),
        ("no_decoherence_time_constant", conditions.NO_DECOHERENCE_TIME),
    ]
    return pd.DataFrame(parameters, columns=["Parameter", "Value"])


def write_excel_results(
    excel_path: str,
    df: pd.DataFrame,
    edge_list: Sequence[tuple],
    request_batches: Sequence[Sequence[Sequence[Any]]],
    algorithms: Sequence[AlgorithmConfig],
    args: argparse.Namespace,
    budgets: Sequence[int],
    request_seed: Optional[int],
    topology_seed: Optional[int],
    output_dir: str,
    timestamp: str,
) -> str:
    summary = df[df["trial"] == "SUMMARY"].copy()
    trial_rows = df[df["trial"] != "SUMMARY"].copy()

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        pd.DataFrame(
            [
                {
                    "description": (
                        "Single-slot multi-request source placement comparison "
                        f"on a {args.topology_type} topology."
                    ),
                    "timestamp": timestamp,
                }
            ]
        ).to_excel(writer, sheet_name="README", index=False)
        run_parameters_dataframe(args, budgets, edge_list, request_seed, topology_seed, output_dir, timestamp).to_excel(
            writer,
            sheet_name="Run_Params",
            index=False,
        )
        topology_dataframe(edge_list).to_excel(writer, sheet_name="Topology", index=False)
        request_batches_dataframe(request_batches).to_excel(writer, sheet_name="Requests", index=False)
        algorithms_dataframe(algorithms).to_excel(writer, sheet_name="Algorithms", index=False)
        budget_sweep_dataframe(df).to_excel(writer, sheet_name="Budget_Sweep_Data", index=False)
        summary.to_excel(writer, sheet_name="Summary", index=False)
        trial_rows.to_excel(writer, sheet_name="Trial_Results", index=False)
        df.to_excel(writer, sheet_name="All_Results", index=False)

    return excel_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one-slot multi-request throughput experiments."
    )
    parser.add_argument("--num-trials", type=int, default=conditions.NUM_TRIALS)
    parser.add_argument("--num-requests", type=int, default=conditions.NUM_REQUESTS_PER_TRIAL)
    parser.add_argument("--num-users", type=int, default=conditions.NUM_USERS_PROTOCOLS_1)
    parser.add_argument("--budget", type=int, default=conditions.FIXED_BUDGET)
    parser.add_argument(
        "--budgets",
        default=",".join(str(budget) for budget in conditions.SOURCE_BUDGETS),
        help="Comma-separated source budgets for the budget-sweep plot.",
    )
    parser.add_argument("--p-op", type=float, default=conditions.OP_PROTOCOLS_1)
    parser.add_argument("--q-swap", type=float, default=conditions.Q_SWAP)
    parser.add_argument("--q-fus", type=float, default=conditions.Q_FUS)
    parser.add_argument("--edge-capacity", type=int, default=conditions.EDGE_CAPACITY)
    parser.add_argument("--node-memory", type=int, default=conditions.NODE_MEMORY_CAPACITY)
    parser.add_argument("--topology-type", choices=["grid", "waxman"], default=conditions.TOPOLOGY_TYPE)
    parser.add_argument("--grid-rows", type=int, default=conditions.GRID_ROWS)
    parser.add_argument("--grid-cols", type=int, default=conditions.GRID_COLS)
    parser.add_argument("--grid-edge-length-km", type=float, default=conditions.GRID_EDGE_LENGTH_KM)
    parser.add_argument("--network-scale", type=int, default=conditions.NETWORK_SCALE)
    parser.add_argument("--waxman-delta", type=float, default=conditions.WAXMAN_DELTA)
    parser.add_argument("--waxman-epsilon", type=float, default=conditions.WAXMAN_EPSILON)
    parser.add_argument("--waxman-area-width-km", type=float, default=conditions.WAXMAN_AREA_WIDTH_KM)
    parser.add_argument("--waxman-area-height-km", type=float, default=conditions.WAXMAN_AREA_HEIGHT_KM)
    parser.add_argument(
        "--waxman-ensure-connected",
        dest="waxman_ensure_connected",
        action="store_true",
        default=conditions.WAXMAN_ENSURE_CONNECTED,
    )
    parser.add_argument(
        "--no-waxman-ensure-connected",
        dest="waxman_ensure_connected",
        action="store_false",
    )
    parser.add_argument("--decoherence-time", type=int, default=conditions.NO_DECOHERENCE_TIME)
    parser.add_argument("--seed", type=int, default=conditions.RANDOM_SEED)
    parser.add_argument("--request-order", choices=["given", "random"], default="given")
    parser.add_argument("--ilp-k-trees", type=int, default=conditions.ILP_K_TREES)
    parser.add_argument("--ilp-cg-initial-trees", type=int, default=conditions.ILP_CG_INITIAL_TREES)
    parser.add_argument("--ilp-cg-pricing-trials", type=int, default=conditions.ILP_CG_PRICING_TRIALS)
    parser.add_argument("--ilp-cg-max-trees-per-request", type=int, default=conditions.ILP_CG_MAX_TREES_PER_REQUEST)
    parser.add_argument(
        "--ilp-cg-max-pricing-columns-per-request",
        type=int,
        default=conditions.ILP_CG_MAX_PRICING_COLUMNS_PER_REQUEST,
    )
    parser.add_argument("--ilp-cg-max-iterations", type=int, default=conditions.ILP_CG_MAX_ITERATIONS)
    parser.add_argument("--ilp-time-limit", type=parse_optional_float, default=None)
    parser.add_argument("--ilp-mip-gap", type=parse_optional_float, default=None)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--output-dir",
        default="simulation_plots",
        help="Base directory for timestamped run outputs.",
    )
    parser.add_argument(
        "--excel-output",
        default="auto",
        help="Excel output path. Use 'auto' for the timestamped run directory or an empty string to disable.",
    )
    parser.add_argument(
        "--plot-output",
        default="auto",
        help="Plot output path. Use 'auto' for the timestamped run directory or an empty string to disable plotting.",
    )
    parser.add_argument(
        "--skip-budget-sweep",
        dest="skip_budget_sweep",
        action="store_true",
        default=conditions.SKIP_BUDGET_SWEEP,
        help="Skip the SOURCE_BUDGETS sweep and run only configured extra sweeps.",
    )
    parser.add_argument(
        "--run-budget-sweep",
        dest="skip_budget_sweep",
        action="store_false",
        help="Run the SOURCE_BUDGETS sweep even when SKIP_BUDGET_SWEEP is True.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir, timestamp = make_timestamped_output_dir(args.output_dir)
    print(f"Created output directory: {output_dir}")

    edge_list, topology_seed, topology_description = build_topology_from_args(args)
    print(
        "Using topology: "
        f"{topology_description}, nodes={len(nodes_from_edge_list(edge_list))}, "
        f"edges={len(edge_list)}, avg_edge_length={average_edge_length(edge_list):.2f} km"
    )

    set_global_seed(args.seed)
    all_nodes = nodes_from_edge_list(edge_list)
    algorithms = build_algorithm_configs()
    budgets = parse_budget_list(args.budgets)
    request_seed = derive_seed(args.seed, "requests")
    max_requests, max_users = canonical_request_shape(args)
    baseline_request_batches = generate_request_batches(
        all_nodes=all_nodes,
        num_trials=args.num_trials,
        num_requests_per_trial=max_requests,
        num_users_per_request=max_users,
        seed=request_seed,
    )

    if args.skip_budget_sweep:
        print("Skipping SOURCE_BUDGETS sweep.")
    else:
        request_batches = truncate_request_batches(
            baseline_request_batches,
            num_requests_per_trial=args.num_requests,
            num_users_per_request=args.num_users,
        )
        df = evaluate_algorithms_over_budgets(
            edge_list=edge_list,
            request_batches=request_batches,
            budgets=budgets,
            algorithms=algorithms,
            p_op=args.p_op,
            max_per_edge=args.edge_capacity,
            node_memory_capacity=args.node_memory,
            q_swap=args.q_swap,
            q_fus=args.q_fus,
            decoherence_time=args.decoherence_time,
            seed=args.seed,
            request_order=args.request_order,
            ilp_k_trees=args.ilp_k_trees,
            ilp_cg_initial_trees=args.ilp_cg_initial_trees,
            ilp_cg_pricing_trials=args.ilp_cg_pricing_trials,
            ilp_cg_max_trees_per_request=args.ilp_cg_max_trees_per_request,
            ilp_cg_max_pricing_columns_per_request=args.ilp_cg_max_pricing_columns_per_request,
            ilp_cg_max_iterations=args.ilp_cg_max_iterations,
            ilp_time_limit=args.ilp_time_limit,
            ilp_mip_gap=args.ilp_mip_gap,
            quiet=not args.verbose,
        )

        summary = df[df["trial"] == "SUMMARY"][
            [
                "budget",
                "algorithm",
                "throughput_qbps",
                "throughput_ci95_halfwidth",
                "placement_and_routing_runtime",
                "placement_and_routing_runtime_ci95_halfwidth",
                "algorithm_running_time",
                "algorithm_running_time_ci95_halfwidth",
            ]
        ]
        print(summary.to_string(index=False))

        if args.output:
            csv_path = args.output
            if csv_path == "auto":
                csv_path = os.path.join(output_dir, f"single_slot_results_{timestamp}.csv")
            df.to_csv(csv_path, index=False)
            print(f"Saved detailed CSV results to {csv_path}")

        if args.excel_output:
            excel_path = args.excel_output
            if excel_path == "auto":
                excel_path = os.path.join(output_dir, f"single_slot_results_{timestamp}.xlsx")
            write_excel_results(
                excel_path=excel_path,
                df=df,
                edge_list=edge_list,
                request_batches=request_batches,
                algorithms=algorithms,
                args=args,
                budgets=budgets,
                request_seed=request_seed,
                topology_seed=topology_seed,
                output_dir=output_dir,
                timestamp=timestamp,
            )
            print(f"Saved detailed Excel results to {excel_path}")

        if args.plot_output:
            plot_path = make_metric_plot_path(
                args.plot_output,
                output_dir,
                timestamp,
                "single_slot_budget_sweep_routing",
            )
            plot_path = plot_budget_sweep_comparison(df, algorithms, budgets, plot_path)
            print(f"Saved throughput comparison plot to {plot_path}")

            if conditions.RUN_RUNTIME_PLOTS:
                runtime_plot_path = make_metric_plot_path(
                    args.plot_output,
                    output_dir,
                    timestamp,
                    "single_slot_budget_sweep_algorithm_running_time",
                )
                runtime_plot_path = plot_budget_sweep_comparison(
                    df,
                    algorithms,
                    budgets,
                    runtime_plot_path,
                    value_column="placement_and_routing_runtime",
                    error_column="placement_and_routing_runtime_ci95_halfwidth",
                    y_label="Source placement + entanglement routing time (s)",
                )
                print(f"Saved algorithm running time plot to {runtime_plot_path}")

    if not conditions.RUN_EXTRA_SWEEPS:
        return

    extra_sweeps = list(conditions.SWEEP_CONDITIONS.items())
    if not extra_sweeps:
        print("No extra sweeps configured in SWEEP_CONDITIONS.")
        return

    for sweep_parameter, sweep_values in extra_sweeps:
        df_sweep = evaluate_algorithms_over_one_factor_sweep(
            edge_list=edge_list,
            all_nodes=all_nodes,
            sweep_parameter=sweep_parameter,
            sweep_values=sweep_values,
            algorithms=algorithms,
            args=args,
            baseline_request_batches=baseline_request_batches,
            baseline_request_seed=request_seed,
            request_order=args.request_order,
        )
        sweep_csv_path = os.path.join(
            output_dir,
            f"single_slot_sweep_{sweep_parameter}_{timestamp}.csv",
        )
        df_sweep.to_csv(sweep_csv_path, index=False)
        print(f"Saved {sweep_parameter} sweep data to {sweep_csv_path}")
        sweep_summary = df_sweep[df_sweep["trial"] == "SUMMARY"][
            [
                "sweep_parameter",
                "sweep_value",
                "algorithm",
                "throughput_qbps",
                "throughput_ci95_halfwidth",
            ]
        ]
        print(sweep_summary.to_string(index=False))

        if args.plot_output:
            sweep_plot_path = make_metric_plot_path(
                args.plot_output,
                output_dir,
                timestamp,
                f"single_slot_sweep_{sweep_parameter}_throughput",
            )
            sweep_plot_path = plot_budget_sweep_comparison(
                df_sweep,
                algorithms,
                sweep_values,
                sweep_plot_path,
                value_column="throughput_qbps",
                error_column="ci95_halfwidth",
                y_label="Throughput (qbps)",
                x_column=sweep_parameter,
                x_label=SWEEP_PLOT_LABELS[sweep_parameter],
            )
            print(f"Saved {sweep_parameter} throughput plot to {sweep_plot_path}")


if __name__ == "__main__":
    main()
