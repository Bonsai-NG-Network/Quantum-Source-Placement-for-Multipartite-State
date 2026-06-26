"""
Sanity checks for the source-placement ILP expected-throughput objective.

These checks do not replace the stochastic simulator. They verify properties
of the ILP-side expected objective:

1. With p_op=q_swap=q_fus=1 and zero-length links, each selected candidate
   tree has rho=1, so the expected-throughput term equals the selected-tree
   count.
2. Increasing source budget should not decrease the ILP expected objective.

Run:
    D:/anaconda3/envs/pytorch/python.exe sanity_check_ilp_expected_objective.py
"""

from __future__ import annotations

from ilp_multipartite_source_placement import solve_single_slot_ilp_request_batch


def main() -> None:
    edge_list = [
        (0, 1, 0.0),
        (1, 2, 0.0),
        (0, 3, 0.0),
        (3, 4, 0.0),
        (2, 4, 0.0),
        (1, 3, 0.0),
    ]
    request_batch = [[0, 2, 4], [0, 1, 3]]

    low_budget = solve_single_slot_ilp_request_batch(
        edge_list=edge_list,
        request_batch=request_batch,
        source_budget=4,
        max_sources_per_edge=4,
        node_memory_capacity=10,
        k_trees_per_request=4,
        p_op=1.0,
        q_swap=1.0,
        q_fus=1.0,
        max_trees_per_request=2,
        master_seed=1,
        time_limit=10,
        verbose=False,
    )
    high_budget = solve_single_slot_ilp_request_batch(
        edge_list=edge_list,
        request_batch=request_batch,
        source_budget=8,
        max_sources_per_edge=4,
        node_memory_capacity=10,
        k_trees_per_request=4,
        p_op=1.0,
        q_swap=1.0,
        q_fus=1.0,
        max_trees_per_request=2,
        master_seed=1,
        time_limit=10,
        verbose=False,
    )

    for name, result in {"low_budget": low_budget, "high_budget": high_budget}.items():
        assert result["status_name"] in {"OPTIMAL", "SUBOPTIMAL", "TIME_LIMIT"}, (
            name,
            result["status_name"],
        )
        expected_term = float(result["ilp_expected_throughput_term"])
        selected_count = int(result["ilp_selected_tree_count"])
        assert abs(expected_term - selected_count) <= 1e-8, (
            name,
            expected_term,
            selected_count,
        )

    assert (
        float(high_budget["ilp_expected_objective"])
        + 1e-8
        >= float(low_budget["ilp_expected_objective"])
    ), (
        low_budget["ilp_expected_objective"],
        high_budget["ilp_expected_objective"],
    )

    print("ILP expected-objective sanity checks passed.")
    print(
        "low_budget:",
        {
            "ilp_expected_objective": low_budget["ilp_expected_objective"],
            "ilp_expected_throughput_term": low_budget["ilp_expected_throughput_term"],
            "ilp_covered_requests": low_budget["ilp_covered_requests"],
            "ilp_selected_tree_count": low_budget["ilp_selected_tree_count"],
        },
    )
    print(
        "high_budget:",
        {
            "ilp_expected_objective": high_budget["ilp_expected_objective"],
            "ilp_expected_throughput_term": high_budget["ilp_expected_throughput_term"],
            "ilp_covered_requests": high_budget["ilp_covered_requests"],
            "ilp_selected_tree_count": high_budget["ilp_selected_tree_count"],
        },
    )


if __name__ == "__main__":
    main()
