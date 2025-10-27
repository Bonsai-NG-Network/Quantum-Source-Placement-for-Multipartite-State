import networkx as nx
from quantum_network import QuantumNetwork
from entanglement_swapping import EntanglementSwapping
from entanglement_fusion import EntanglementFusion
from quantum_source_placement import SourcePlacement
from steiner_tree_algorithms import approximate_steiner_tree, has_connecting_tree
from collections import Counter
import matplotlib.pyplot as plt


class MPGreedyRouting:
    def __init__(self, network, user_set, p_op):
        self.p_op = p_op
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

    def mp_greedy_routing(self, vc, max_timeslot, deployed_sources):
        time_slot = 0
        hasGHZ = False

        while not hasGHZ:
            time_slot = time_slot + 1
            print(f"\n[MultiplePathGreedy] [Time slot {time_slot}]")
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

            S_prime = [u for u in self.user_set if u != vc and not self._has_shared_bell_pair(u, vc)]
            for s in S_prime:
                path = paths.get(s, [])
                if path:
                    # Swapping logic needs to handle multiple links per edge
                    self.swapping.entanglement_swapping(path=path, current_time=time_slot, p_op=self.p_op)

            remote_users = [u for u in self.user_set if u != vc]
            if all(self._has_shared_bell_pair(u, vc) for u in remote_users):
                success = self.fusion.fuse_users(vc, user_list=self.user_set, current_time=time_slot, p_op=self.p_op)
                if success:
                    print(f"[Fusion] GHZ generated at vc={vc}")
                    hasGHZ = True

        return time_slot


class MPCooperativeRouting:
    def __init__(self, network, user_set, p_op):
        self.p_op = p_op
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

    def mpc_routing(self, max_timeslot, deployed_sources):
        time_slot = 0
        hasGHZ = False

        while not hasGHZ:
            time_slot += 1
            print(f"\n[MultiplePathCooperative] [Time slot {time_slot}]")
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
                used_links = set(R.edges())

                success = self.fusion.fuse_users_from_tree(user_list=self.user_set, tree_links=used_links, current_time=time_slot, p_op=self.p_op)
                if success:
                    print(f"[Fusion] GHZ generated via a Steiner tree.")
                    hasGHZ = True

        return time_slot


class MPPackingRouting:
    def __init__(self, network, user_set, p_op):
        self.p_op = p_op
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

    def mpp_routing(self, max_timeslot, deployed_sources):
        time_slot = 0
        hasGHZ = False
        num_ghz_in_slot = 0

        while not hasGHZ:
            time_slot += 1
            print(f"\n[MultiplePathPacking] [Time slot {time_slot}]")
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
                used_links = set(R.edges())

                success = self.fusion.fuse_users_from_tree(self.user_set, used_links, time_slot, self.p_op)

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
#     source = SourcePlacement(net.topo)
#     sources = source.place_sources_for_request(users)
#     for u, v in sources:
#         net.attempt_entanglement(u, v, p_op=0.9, gen_time=0, flag=True)
#
#     net.show_network_status(current_time=0)
#
#     MPGrouting = MPGreedyRouting(net, users, p_op=0.9)
#     print("\n[MultiPath_G+ Routing Test]")
#     final_time, cost = MPGrouting.mp_greedy_routing(vc, max_timeslot)
#     if final_time:
#         print(f"[SUCCESS] GHZ state generated at time slot {final_time}")
#     else:
#         print("[FAILURE] Protocol did not succeed within time limit")
#
#     net.show_network_status(current_time=final_time)