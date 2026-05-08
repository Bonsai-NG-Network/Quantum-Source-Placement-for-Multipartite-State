"""
Monte Carlo probability simulation for request-based Steiner trees.

For each request (user set), this module:
1) Uses SourcePlacementDP to deploy source pairs under a budget.
2) Generates multiple diverse Steiner trees for the same request.
3) Samples link-generation success many times.
4) Estimates per-tree success probability and request success probability.

Tree success means every edge in the tree has at least one successful source pair.
Request success means at least one generated tree succeeds.
"""

import argparse
import csv
import random
from collections import Counter, defaultdict

import networkx as nx

from network_request import RequestGenerator
from network_topology import Topology
from quantum_source_placement_dp import SourcePlacementDP, _normalize_edge_tuple
from steiner_tree_algorithms import approximate_steiner_tree


DEFAULT_EDGE_LIST_3X3 = [
    (0, 1, 10), (0, 3, 10), (1, 2, 10), (1, 4, 10),
    (2, 5, 10), (3, 4, 10), (3, 6, 10), (4, 7, 10),
    (5, 8, 10), (6, 7, 10), (7, 8, 10),
]


class RequestTreeMonteCarlo:
    def __init__(
        self,
        topo,
        p_op=0.8,
        loss_coef_dB_per_km=0.2,
        cost_budget=12,
        max_per_edge=3,
        k_trees=5,
        num_samples=10000,
        seed=None,
    ):
        self.topo = topo
        self.p_op = p_op
        self.loss_coef_dB_per_km = loss_coef_dB_per_km
        self.cost_budget = cost_budget
        self.max_per_edge = max_per_edge
        self.k_trees = k_trees
        self.num_samples = num_samples
        self.rng = random.Random(seed)
        if seed is not None:
            random.seed(seed)

    def _edge_success_prob(self, edge):
        placer = SourcePlacementDP(self.topo)
        return placer._edge_success_prob(
            edge,
            p_op=self.p_op,
            loss_coef_dB_per_km=self.loss_coef_dB_per_km,
        )

    def edge_success_probabilities(self):
        return {
            _normalize_edge_tuple((u, v)): self._edge_success_prob((u, v))
            for u, v in self.topo.graph.edges()
        }

    def generate_request_trees(self, user_set):
        """
        Generate up to k_trees unique diverse Steiner trees for one request.

        This follows the idea in SourcePlacementDP: previously used edges get
        larger temporary weights, and a small random jitter breaks ties.
        """
        G = self.topo.graph.copy()
        used_count = defaultdict(int)
        unique = {}

        for _ in range(self.k_trees):
            for u, v, data in G.edges(data=True):
                base_w = float(data.get("length_km", data.get("weight", 1.0)))
                edge_key = _normalize_edge_tuple((u, v))
                overlap_penalty = 1.0 + 0.8 * used_count[edge_key]
                jitter = 1.0 + 0.02 * self.rng.random()
                data["_mc_tmp_w"] = base_w * overlap_penalty * jitter

            try:
                tree = approximate_steiner_tree(G, user_set, weight_key="_mc_tmp_w")
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue

            tree_edges = frozenset(_normalize_edge_tuple(e) for e in tree.edges())
            if not tree_edges:
                continue

            unique[tree_edges] = sorted(tree_edges)
            for edge in tree_edges:
                used_count[edge] += 1

        for _, _, data in G.edges(data=True):
            data.pop("_mc_tmp_w", None)

        return list(unique.values())

    def place_sources(self, user_set):
        placer = SourcePlacementDP(self.topo)
        sources, debug = placer.place_sources_for_request(
            user_set=user_set,
            cost_budget=self.cost_budget,
            max_per_edge=self.max_per_edge,
            K_steiner=self.k_trees,
            k_paths=2,
            weight_attr="length_km",
            w_topo=0.25,
            w_demand=0.35,
            w_quality=0.4,
            w_overlap=0.0,
            p_map=None,
            p_op=self.p_op,
            value_model="prob",
        )
        allocation = dict(Counter(_normalize_edge_tuple(e) for e in sources))
        return allocation, debug

    @staticmethod
    def tree_probability(tree_edges, allocation, edge_probs):
        prob = 1.0
        for edge in tree_edges:
            y = allocation.get(edge, 0)
            p_e = edge_probs.get(edge, 0.0)
            q_e = 1.0 - (1.0 - p_e) ** y
            prob *= q_e
        return prob

    def simulate_request(self, user_set):
        trees = self.generate_request_trees(user_set)
        allocation, debug = self.place_sources(user_set)
        edge_probs = self.edge_success_probabilities()

        tree_success_counts = [0 for _ in trees]
        request_success_count = 0

        deployed_edges = sorted(allocation)
        for _ in range(self.num_samples):
            active_edges = set()
            for edge in deployed_edges:
                p_e = edge_probs.get(edge, 0.0)
                pairs = allocation.get(edge, 0)
                edge_active = any(self.rng.random() < p_e for _ in range(pairs))
                if edge_active:
                    active_edges.add(edge)

            request_success = False
            for idx, tree_edges in enumerate(trees):
                if all(edge in active_edges for edge in tree_edges):
                    tree_success_counts[idx] += 1
                    request_success = True

            if request_success:
                request_success_count += 1

        tree_rows = []
        for idx, tree_edges in enumerate(trees):
            tree_rows.append({
                "tree_id": idx,
                "edges": tree_edges,
                "num_edges": len(tree_edges),
                "mc_success_probability": tree_success_counts[idx] / self.num_samples,
                "theoretical_success_probability": self.tree_probability(
                    tree_edges, allocation, edge_probs
                ),
            })

        return {
            "user_set": list(user_set),
            "num_trees": len(trees),
            "num_samples": self.num_samples,
            "request_success_probability": request_success_count / self.num_samples,
            "tree_results": tree_rows,
            "allocation": allocation,
            "dp_debug": debug,
        }

    def simulate_requests(self, request_list):
        results = []
        for user_set in request_list:
            results.append(self.simulate_request(user_set))
        return results


def build_grid_edge_list(rows, cols, length_km):
    edge_list = []
    for r in range(rows):
        for c in range(cols):
            node = r * cols + c
            if c + 1 < cols:
                edge_list.append((node, node + 1, length_km))
            if r + 1 < rows:
                edge_list.append((node, node + cols, length_km))
    return edge_list


def write_summary_csv(results, path):
    rows = []
    for request_id, result in enumerate(results):
        for tree in result["tree_results"]:
            rows.append({
                "request_id": request_id,
                "user_set": result["user_set"],
                "request_success_probability": result["request_success_probability"],
                "tree_id": tree["tree_id"],
                "tree_edges": tree["edges"],
                "tree_num_edges": tree["num_edges"],
                "tree_mc_success_probability": tree["mc_success_probability"],
                "tree_theoretical_success_probability": tree["theoretical_success_probability"],
                "allocation": result["allocation"],
            })

    with open(path, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "request_id",
            "user_set",
            "request_success_probability",
            "tree_id",
            "tree_edges",
            "tree_num_edges",
            "tree_mc_success_probability",
            "tree_theoretical_success_probability",
            "allocation",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Monte Carlo simulation for request-based Steiner tree success probability."
    )
    parser.add_argument("--rows", type=int, default=3)
    parser.add_argument("--cols", type=int, default=3)
    parser.add_argument("--edge-length-km", type=float, default=10.0)
    parser.add_argument("--num-requests", type=int, default=5)
    parser.add_argument("--num-users", type=int, default=3)
    parser.add_argument(
        "--users",
        type=str,
        default=None,
        help="Optional fixed request, e.g. '1,6,5'. If set, --num-requests is ignored.",
    )
    parser.add_argument("--k-trees", type=int, default=5)
    parser.add_argument("--samples", type=int, default=10000)
    parser.add_argument("--budget", type=int, default=12)
    parser.add_argument("--max-per-edge", type=int, default=3)
    parser.add_argument("--p-op", type=float, default=0.8)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--csv", type=str, default=None)
    return parser.parse_args()


def parse_user_list(raw):
    users = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            users.append(int(item))
        except ValueError:
            users.append(item)
    return users


def main():
    args = parse_args()
    edge_list = build_grid_edge_list(args.rows, args.cols, args.edge_length_km)
    topo = Topology(edge_list)

    if args.users:
        requests = [parse_user_list(args.users)]
    else:
        request_gen = RequestGenerator(topo.get_nodes())
        random.seed(args.seed)
        requests = [request_gen.random_users(args.num_users) for _ in range(args.num_requests)]

    simulator = RequestTreeMonteCarlo(
        topo=topo,
        p_op=args.p_op,
        cost_budget=args.budget,
        max_per_edge=args.max_per_edge,
        k_trees=args.k_trees,
        num_samples=args.samples,
        seed=args.seed,
    )
    results = simulator.simulate_requests(requests)

    for request_id, result in enumerate(results):
        print("\n" + "=" * 80)
        print(f"Request {request_id}: users={result['user_set']}")
        print(f"Generated trees: {result['num_trees']}")
        print(f"Allocation: {result['allocation']}")
        print(f"Request success probability: {result['request_success_probability']:.6f}")
        for tree in result["tree_results"]:
            print(
                f"  Tree {tree['tree_id']}: "
                f"MC={tree['mc_success_probability']:.6f}, "
                f"Theory={tree['theoretical_success_probability']:.6f}, "
                f"edges={tree['edges']}"
            )

    if results:
        avg_prob = sum(r["request_success_probability"] for r in results) / len(results)
        print("\n" + "-" * 80)
        print(f"Average request success probability: {avg_prob:.6f}")

    if args.csv:
        write_summary_csv(results, args.csv)
        print(f"\nCSV saved to: {args.csv}")


if __name__ == "__main__":
    main()
