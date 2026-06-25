import networkx as nx
from quantum_network import QuantumNetwork
from entanglement_swapping import EntanglementSwapping
from entanglement_fusion import EntanglementFusion
from steiner_tree_algorithms import approximate_steiner_tree, has_connecting_tree
from tree_operation_planner import (
    build_tree_from_paths,
    build_tree_operation_plan,
    execute_tree_operation_plan,
)
from collections import Counter
import matplotlib.pyplot as plt


class MultipathStarRouting:
    def __init__(self, network, user_set, p_op, q_swap=1.0, q_fus=1.0):
        self.p_op = p_op
        self.q_swap = q_swap
        self.q_fus = q_fus
        self.network = network
        self.G = self.network.topo  # original physical topology
        self.link_manager = network.entanglementlink_manager
        self.user_set = user_set
        self.swapping = EntanglementSwapping(self.network)
        self.fusion = EntanglementFusion(self.network)

    def simulate_entanglement_links(self, deployed_dict, time_slot):
        for edge, k in deployed_dict.items():
            edge_key = tuple(sorted(edge))

            current_count = sum(
                1 for link in self.link_manager.links
                if tuple(sorted(link.nodes)) == edge_key
            )
            remaining = k - current_count
            if remaining <= 0:
                continue

            u, v = edge_key
            for _ in range(remaining):
                self.network.attempt_entanglement(u, v, p_op=self.p_op, gen_time=time_slot)

    def _has_shared_bell_pair(self, user, vc):
        mem = self.network.nodes[user].memory.memory_storage
        # Check if the memory for user has any links to vc
        return vc in mem and len(mem[vc]) > 0

    def get_shortest_paths_MP(self, v, subgraph):
        paths = {}
        for s in self.user_set:
            if s == v:
                continue
            if v not in subgraph.nodes or s not in subgraph.nodes:
                paths[s] = []
                print(f"  No path to {s} because either {v} or {s} is not in the subgraph.")
                continue
            try:
                path = nx.shortest_path(subgraph, source=v, target=s)
                paths[s] = path
            except nx.NetworkXNoPath:
                paths[s] = []
        return paths

    def multipath_star_routing(self, vc, max_timeslot, deployed_sources):
        time_slot = 0
        hasGHZ = False

        while not hasGHZ:
            time_slot = time_slot + 1
            print(f"\n[MultipathStar] [Time slot {time_slot}]")
            if time_slot >= max_timeslot:
                time_slot = 0
                break

            self.network.purge_all_expired(time_slot)
            self.simulate_entanglement_links(deployed_sources, time_slot)

            G_prime = self.link_manager.get_subgraph(current_time=time_slot)
            
            # The logic below needs to be updated to handle multiple links per edge
            paths = self.get_shortest_paths_MP(vc, G_prime)
            print(f"\n[Routing] Selected center: {vc}")
            for s, path in paths.items():
                print(f"  {vc} -> {s}: {path}")

            if all(path for path in paths.values()):
                star_tree = build_tree_from_paths(paths)
                plan = build_tree_operation_plan(
                    star_tree,
                    self.user_set,
                    p_op=1.0,
                    q_swap=self.q_swap,
                    q_fus=self.q_fus,
                    forced_fusion_nodes=[vc],
                )
                success = execute_tree_operation_plan(
                    self.swapping,
                    self.fusion,
                    plan,
                    current_time=time_slot,
                    q_swap=self.q_swap,
                    q_fus=self.q_fus,
                )
                if success:
                    print(f"[Fusion] GHZ generated at vc={vc}")
                    hasGHZ = True

        return time_slot

    def mp_greedy_routing(self, vc, max_timeslot, deployed_sources):
        return self.multipath_star_routing(vc, max_timeslot, deployed_sources)


class MultipathTreeRouting:
    def __init__(self, network, user_set, p_op, q_swap=1.0, q_fus=1.0):
        self.p_op = p_op
        self.q_swap = q_swap
        self.q_fus = q_fus
        self.network = network
        self.G = self.network.topo.graph  # original physical topology
        self.link_manager = network.entanglementlink_manager
        self.user_set = user_set
        self.swapping = EntanglementSwapping(self.network)
        self.fusion = EntanglementFusion(self.network)

    def simulate_entanglement_links(self, deployed_dict, time_slot):
        for edge, k in deployed_dict.items():
            edge_key = tuple(sorted(edge))

            current_count = sum(
                1 for link in self.link_manager.links
                if tuple(sorted(link.nodes)) == edge_key
            )
            remaining = k - current_count
            if remaining <= 0:
                continue

            u, v = edge_key
            for _ in range(remaining):
                self.network.attempt_entanglement(u, v, p_op=self.p_op, gen_time=time_slot)

    def _has_entanglement_link(self, u, v):
        for link in self.link_manager.links:
            if u in link.nodes and v in link.nodes:
                return True
        return False

    def multipath_tree_routing(self, max_timeslot, deployed_sources):
        time_slot = 0
        hasGHZ = False

        while not hasGHZ:
            time_slot += 1
            print(f"\n[MultipathTree] [Time slot {time_slot}]")
            if time_slot > max_timeslot:
                time_slot = 0
                break

            self.network.purge_all_expired(time_slot)
            self.simulate_entanglement_links(deployed_sources, time_slot)

            G_prime = self.link_manager.get_subgraph(current_time=time_slot)
            # print(
            #     f"[MPC] Time {time_slot}, subgraph nodes: {list(G_prime.nodes())}, edges: {list(G_prime.edges(data=True))}")
            # labels = {node: str(node) for node in G_prime.nodes()}
            # pos = {(x, y): (y, -x) for x, y in G_prime.nodes()}
            # fig, ax = plt.subplots(figsize=(6, 6))
            # nx.draw(G_prime, pos, ax=ax, with_labels=True, labels=labels, node_size=500, node_color="skyblue",
            #         font_size=8, font_color="black")
            # ax.set_title(f"Subgraph Visualization at [time slot {time_slot}]")
            # plt.tight_layout()
            # plt.show()

            if has_connecting_tree(G_prime, self.user_set):
                R = approximate_steiner_tree(G_prime, self.user_set)
                print(f"Steiner tree is {R}")
                plan = build_tree_operation_plan(R, self.user_set, p_op=1.0, q_swap=self.q_swap, q_fus=self.q_fus)
                success = execute_tree_operation_plan(
                    self.swapping,
                    self.fusion,
                    plan,
                    current_time=time_slot,
                    q_swap=self.q_swap,
                    q_fus=self.q_fus,
                )
                if success:
                    print(f"[Fusion] GHZ generated via a Steiner tree.")
                    hasGHZ = True

        return time_slot

    def mpc_routing(self, max_timeslot, deployed_sources):
        return self.multipath_tree_routing(max_timeslot, deployed_sources)


class MultipathTreePackingRouting:
    def __init__(self, network, user_set, p_op, q_swap=1.0, q_fus=1.0):
        self.p_op = p_op
        self.q_swap = q_swap
        self.q_fus = q_fus
        self.network = network
        self.G = self.network.topo.graph
        self.link_manager = network.entanglementlink_manager
        self.user_set = user_set
        self.swapping = EntanglementSwapping(self.network)
        self.fusion = EntanglementFusion(self.network)

    def simulate_entanglement_links(self, deployed_dict, time_slot):
        for edge, k in deployed_dict.items():
            edge_key = tuple(sorted(edge))

            current_count = sum(
                1 for link in self.link_manager.links
                if tuple(sorted(link.nodes)) == edge_key
            )
            remaining = k - current_count
            if remaining <= 0:
                continue

            u, v = edge_key
            for _ in range(remaining):
                self.network.attempt_entanglement(u, v, p_op=self.p_op, gen_time=time_slot)

    def _has_entanglement_link(self, u, v):
        for link in self.link_manager.links:
            if u in link.nodes and v in link.nodes:
                return True
        return False

    def multipath_tree_packing_routing(self, max_timeslot, deployed_sources):
        time_slot = 0
        hasGHZ = False
        num_ghz_in_slot = 0

        while not hasGHZ:
            time_slot += 1
            print(f"\n[MultipathTreePacking] [Time slot {time_slot}]")
            if time_slot > max_timeslot:
                time_slot = 0
                break

            self.network.purge_all_expired(time_slot)
            self.simulate_entanglement_links(deployed_sources, time_slot)

            G_prime = self.link_manager.get_subgraph(current_time=time_slot)

            # print(
            #     f"[MPP] Time {time_slot}, subgraph nodes: {list(G_prime.nodes())}, edges: {list(G_prime.edges(data=True))}")
            # labels = {node: str(node) for node in G_prime.nodes()}
            # pos = {(x, y): (y, -x) for x, y in G_prime.nodes()}
            # fig, ax = plt.subplots(figsize=(6, 6))
            # nx.draw(G_prime, pos, ax=ax, with_labels=True, labels=labels, node_size=500, node_color="skyblue",
            #         font_size=8, font_color="black")
            # ax.set_title(f"Subgraph Visualization at [time slot {time_slot}]")
            # plt.tight_layout()
            # plt.show()

            while has_connecting_tree(G_prime, self.user_set):
                R = approximate_steiner_tree(G_prime, self.user_set)
                plan = build_tree_operation_plan(R, self.user_set, p_op=1.0, q_swap=self.q_swap, q_fus=self.q_fus)

                success = execute_tree_operation_plan(
                    self.swapping,
                    self.fusion,
                    plan,
                    current_time=time_slot,
                    q_swap=self.q_swap,
                    q_fus=self.q_fus,
                )

                if success:
                    print(f"[Fusion] GHZ generated via a Steiner tree. (Packing #{num_ghz_in_slot + 1})")
                    num_ghz_in_slot += 1
                    G_prime = self.link_manager.get_subgraph(current_time=time_slot)
                    #
                    # print(f"AFTER! [MPP] Time {time_slot}, subgraph nodes: {list(G_prime.nodes())}, edges: {list(G_prime.edges(data=True))}")
                    # labels = {node: str(node) for node in G_prime.nodes()}
                    # pos = {(x, y): (y, -x) for x, y in G_prime.nodes()}
                    # fig, ax = plt.subplots(figsize=(6, 6))
                    # nx.draw(G_prime, pos, ax=ax, with_labels=True, labels=labels,node_size=500, node_color="skyblue",
                    #         font_size=8, font_color="black")
                    # ax.set_title(f"AFTER! Subgraph Visualization at [time slot {time_slot}]")
                    # plt.tight_layout()
                    # plt.show()
                else:
                    break

            if num_ghz_in_slot > 0:
                hasGHZ = True

        return time_slot, num_ghz_in_slot

    def mpp_routing(self, max_timeslot, deployed_sources):
        return self.multipath_tree_packing_routing(max_timeslot, deployed_sources)


# if __name__ == "__main__":
#     """
#        0 —— 1 —— 2
#        |    |    |
#        3 —— 4 —— 5
#        |    |    |
#        6 —— 7 —— 8
#     """
#     edge_list = [
#         (0, 1, 10),
#         (0, 3, 10),
#         (1, 2, 10),
#         (1, 4, 10),
#         (2, 5, 10),
#         (3, 4, 10),
#         (3, 6, 10),
#         (4, 5, 10),
#         (4, 7, 10),
#         (5, 8, 10),
#         (6, 7, 10),
#         (7, 8, 10)
#     ]
#     users = [0, 2, 7]
#     vc = 1
#     max_timeslot = 50
#     paths = {0: [1, 0],
#              2: [1, 2],
#              7: [1, 4, 7]}
#
#     net = QuantumNetwork(edge_list=edge_list, memory_size=4, decoherence_time=6)
#
#     source = AllEdgesRoundRobinSourcePlacement(net.topo)
#     sources = source.place_sources_for_request(users)
#     for u, v in sources:
#         net.attempt_entanglement(u, v, p_op=0.9, gen_time=0, flag=True)
#
#     net.show_network_status(current_time=0)
#
#     MPGrouting = MultipathStarRouting(net, users, p_op=0.9)
#     print("\n[MultiPath_G+ Routing Test]")
#     final_time, cost = MPGrouting.mp_greedy_routing(vc, max_timeslot)
#     if final_time:
#         print(f"[SUCCESS] GHZ state generated at time slot {final_time}")
#     else:
#         print("[FAILURE] Protocol did not succeed within time limit")
#
#     net.show_network_status(current_time=final_time)


def main():
    edge_list = [
        ("A", "X", 0),
        ("X", "E", 0),
        ("E", "B", 0),
        ("E", "C", 0),
    ]
    user_set = ["A", "B", "C"]

    network = QuantumNetwork(edge_list=edge_list, max_per_edge=2, decoherence_time=10)
    for u, v, _ in edge_list:
        network.attempt_entanglement(u, v, p_op=1.0, gen_time=0, flag=True)

    tree = nx.Graph()
    for u, v, _ in edge_list:
        tree.add_edge(u, v, length_km=10)

    router = MultipathTreePackingRouting(network, user_set, p_op=1.0, q_swap=1.0, q_fus=1.0)
    plan = build_tree_operation_plan(tree, user_set, p_op=1.0, q_swap=1.0, q_fus=1.0)
    assert plan.swap_nodes == ["X"]
    assert plan.candidate_removal_nodes == ["E"]
    assert execute_tree_operation_plan(
        router.swapping,
        router.fusion,
        plan,
        current_time=1,
        q_swap=1.0,
        q_fus=1.0,
    )

    ghz_links = [link for link in network.entanglementlink_manager.links if link.state_type == "GHZ"]
    assert len(ghz_links) == 1
    assert set(ghz_links[0].nodes) == set(user_set)
    assert network.nodes["A"].get_memory_usage() == 1
    assert network.nodes["B"].get_memory_usage() == 1
    assert network.nodes["C"].get_memory_usage() == 1
    assert network.nodes["E"].get_memory_usage() == 0
    assert network.nodes["X"].get_memory_usage() == 0

    network = QuantumNetwork(edge_list=edge_list, max_per_edge=2, decoherence_time=10)
    for u, v, _ in edge_list:
        network.attempt_entanglement(u, v, p_op=1.0, gen_time=0, flag=True)
    router = MultipathTreePackingRouting(network, user_set, p_op=1.0, q_swap=0.0, q_fus=1.0)
    plan = build_tree_operation_plan(tree, user_set, p_op=1.0, q_swap=0.0, q_fus=1.0)
    assert not execute_tree_operation_plan(
        router.swapping,
        router.fusion,
        plan,
        current_time=1,
        q_swap=0.0,
        q_fus=1.0,
    )
    ghz_links = [link for link in network.entanglementlink_manager.links if link.state_type == "GHZ"]
    assert not ghz_links

    network = QuantumNetwork(edge_list=edge_list, max_per_edge=2, decoherence_time=10)
    deployed = {tuple(sorted((u, v))): 1 for u, v, _ in edge_list}
    router = MultipathTreePackingRouting(network, user_set, p_op=1.0, q_swap=1.0, q_fus=1.0)
    final_time, num_ghz = router.multipath_tree_packing_routing(max_timeslot=4, deployed_sources=deployed)
    assert final_time > 0
    assert num_ghz > 0

    print("multipath_routing main test passed.")


if __name__ == "__main__":
    main()
