"""
Entanglement fusion for multipartite GHZ generation.

The unified interface is fuse_tree(): callers pass the already selected
tree/reduced-tree and the fusion nodes identified by the tree planner.
The old fuse_users() and fuse_users_from_tree() methods are kept as wrappers
for existing routing code.
"""

from __future__ import annotations

import random
from typing import Any, Dict, Iterable, List, Optional

import networkx as nx

from quantum_network import QuantumNetwork


class EntanglementFusion:
    def __init__(self, network):
        self.network = network
        self.link_manager = network.entanglementlink_manager

    def fuse_tree(
        self,
        user_list: Iterable[Any],
        reduced_tree: nx.Graph,
        fusion_nodes: Iterable[Any],
        current_time: int,
        q_fus: float = 1.0,
        q_fus_by_node: Optional[Dict[Any, float]] = None,
        candidate_removal_nodes: Optional[Iterable[Any]] = None,
    ) -> bool:
        user_list = list(user_list)
        fusion_nodes = list(fusion_nodes)
        candidate_removal_nodes = list(candidate_removal_nodes or [])

        links_to_consume = []
        for u, v in reduced_tree.edges():
            link_id = self._find_link_id(u, v)
            if link_id is None:
                print(f"[Fusion] Missing link {u}-{v} in tree.")
                return False
            links_to_consume.append(link_id)

        fusion_success = True
        failed_node = None
        failed_q = None
        for node in fusion_nodes:
            q = self._probability_for_node(node, q_fus, q_fus_by_node)
            if random.random() >= q:
                fusion_success = False
                failed_node = node
                failed_q = q
                break

        self._consume_links(links_to_consume)

        if not fusion_success:
            print(f"[Fusion] Failed at node {failed_node} (q_fus={failed_q:.4f})")
            return False

        success, ghz_link_id = self.link_manager.create_link(
            user_list,
            p_op=1,
            gen_time=current_time,
            length_km=0,
            attr="Fusion",
            flag=True,
            state_type="GHZ",
            operation="FUSION",
            operation_node=fusion_nodes,
            parent_link_ids=links_to_consume,
        )

        if not success:
            return False

        if not self.network.record_ghz_memory(user_list, ghz_link_id, current_time):
            self.link_manager.remove_link_by_id(ghz_link_id)
            return False

        for node in candidate_removal_nodes:
            self._remove_non_user_ghz_memory(node, ghz_link_id)

        print(f"[Fusion] GHZ state created among {user_list} via fusion nodes {fusion_nodes}")
        return True

    def fuse_users(
        self,
        intermediate_node,
        user_list,
        current_time,
        p_op=None,
        q_fus=1.0,
        q_fus_by_node=None,
    ):
        tree = nx.Graph()
        for user in user_list:
            if user != intermediate_node:
                tree.add_edge(user, intermediate_node)

        return self.fuse_tree(
            user_list=user_list,
            reduced_tree=tree,
            fusion_nodes=[intermediate_node],
            current_time=current_time,
            q_fus=q_fus,
            q_fus_by_node=q_fus_by_node,
            candidate_removal_nodes=[] if intermediate_node in user_list else [intermediate_node],
        )

    def fuse_users_from_tree(
        self,
        user_list,
        tree_links,
        current_time,
        p_op=None,
        fusion_nodes=None,
        q_fus=1.0,
        q_fus_by_node=None,
        candidate_removal_nodes=None,
    ):
        tree = nx.Graph()
        tree.add_edges_from(tree_links)

        if fusion_nodes is None:
            fusion_nodes = [node for node in tree.nodes() if tree.degree(node) >= 2]

        if candidate_removal_nodes is None:
            user_set = set(user_list)
            candidate_removal_nodes = [node for node in tree.nodes() if node not in user_set]

        return self.fuse_tree(
            user_list=user_list,
            reduced_tree=tree,
            fusion_nodes=fusion_nodes,
            current_time=current_time,
            q_fus=q_fus,
            q_fus_by_node=q_fus_by_node,
            candidate_removal_nodes=candidate_removal_nodes,
        )

    def _consume_links(self, link_ids: Iterable[str]) -> None:
        for link_id in link_ids:
            self.link_manager.remove_link_by_id(link_id)
            self.network.release_link_memory_everywhere(link_id)

    def _remove_non_user_ghz_memory(self, node_id, ghz_link_id):
        if node_id in self.network.nodes:
            self.network.nodes[node_id].memory.release_by_link_id(ghz_link_id)

    def _find_link_id(self, u, v):
        for link in self.link_manager.links:
            if len(link.nodes) == 2 and u in link.nodes and v in link.nodes:
                return link.link_id
        return None

    def _probability_for_node(self, node_id, q_fus, q_fus_by_node):
        if q_fus_by_node is not None:
            return float(q_fus_by_node.get(node_id, q_fus))
        return float(q_fus)


def main():
    edge_list = [
        ("A", "D", 10),
        ("B", "D", 10),
        ("C", "D", 10),
    ]

    net = QuantumNetwork(edge_list=edge_list, max_per_edge=3, decoherence_time=6)
    net.attempt_entanglement("A", "D", p_op=1.0, gen_time=0, flag=True)
    net.attempt_entanglement("B", "D", p_op=1.0, gen_time=0, flag=True)
    net.attempt_entanglement("C", "D", p_op=1.0, gen_time=0, flag=True)

    fusion = EntanglementFusion(net)
    assert fusion.fuse_users("D", ["A", "B", "C"], current_time=1, q_fus=1.0)
    ghz_links = [link for link in net.entanglementlink_manager.links if link.state_type == "GHZ"]
    assert len(ghz_links) == 1
    assert set(ghz_links[0].nodes) == {"A", "B", "C"}
    assert net.nodes["A"].get_memory_usage() == 1
    assert net.nodes["B"].get_memory_usage() == 1
    assert net.nodes["C"].get_memory_usage() == 1
    assert net.nodes["D"].get_memory_usage() == 0

    net = QuantumNetwork(edge_list=edge_list, max_per_edge=3, decoherence_time=6)
    net.attempt_entanglement("A", "D", p_op=1.0, gen_time=0, flag=True)
    net.attempt_entanglement("B", "D", p_op=1.0, gen_time=0, flag=True)
    net.attempt_entanglement("C", "D", p_op=1.0, gen_time=0, flag=True)
    fusion = EntanglementFusion(net)
    assert not fusion.fuse_users("D", ["A", "B", "C"], current_time=1, q_fus=0.0)
    assert not net.entanglementlink_manager.links
    assert net.nodes["A"].get_memory_usage() == 0
    assert net.nodes["B"].get_memory_usage() == 0
    assert net.nodes["C"].get_memory_usage() == 0
    assert net.nodes["D"].get_memory_usage() == 0

    print("EntanglementFusion main test passed.")


if __name__ == "__main__":
    main()
