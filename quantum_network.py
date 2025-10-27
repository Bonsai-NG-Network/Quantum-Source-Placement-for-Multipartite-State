from network_topology import Topology
from quantum_entity import QuantumNode
from entanglement_link import EntanglementLinkManager


class QuantumNetwork:
    def __init__(self, edge_list, max_per_edge=1, decoherence_time=10):
    # def __init__(self, length_network, width_network, edge_length_km, max_per_edge=1, decoherence_time=10):
        self.topo = Topology(edge_list)
        # self.topo = Topology(length_network, width_network, edge_length_km)
        # self.topo.draw_topology()
        self.nodes = {}
        self.entanglementlink_manager = EntanglementLinkManager(decoherence_time)
        self.max_per_edge = max_per_edge

        for node_id in self.topo.get_nodes():
            self.nodes[node_id] = QuantumNode(node_id, max_per_edge, decoherence_time)

        for u, v in self.topo.get_edges():
            length_km = self.topo.get_edge_length(u, v)
            self.nodes[u].add_channel(u, v, length_km)
            self.nodes[v].add_channel(v, u, length_km)

    def attempt_entanglement(self, node1, node2, p_op, gen_time, attr=None, flag=False):
        if attr is None:
            length_km = self.topo.get_edge_length(node1, node2)
        else:
            # swapping
            length_km = 0
            p_op = 1

        success, link_id = self.entanglementlink_manager.create_link([node1, node2], p_op=p_op, gen_time=gen_time, length_km=length_km, attr=attr, flag=flag)

        if success and self.nodes[node1].node_record_entanglement(peer_id=node2, link_id=link_id, gen_time_slot=gen_time) \
           and self.nodes[node2].node_record_entanglement(peer_id=node1, link_id=link_id, gen_time_slot=gen_time):
            return True, link_id
        return False, None

    def show_network_status(self, current_time):  # update the memory and the entanglement link
        print("\n")
        print("-" * 80)
        print(f"Network Status at [Time Slot {current_time}]")
        for node in self.nodes.values():
            node.show_node_status(current_time)
        self.entanglementlink_manager.show_active_links(current_time)
        print("-" * 80)

    def purge_all_expired(self, current_time):
        for node in self.nodes.values():
            node.node_delete_entanglement(current_time)
        self.entanglementlink_manager.purge_expired_links(current_time)

    def reset(self):
        for node in self.nodes.values():
            node.memory.memory_storage = {}
        self.entanglementlink_manager.links = []
        self.entanglementlink_manager.slot_counter = {}
#
#
# if __name__ == "__main__":
#     # A - B - C - D
#     edge_list = [
#         ("A", "B", 10),
#         ("B", "C", 15),
#         ("C", "D", 20)
#     ]
#
#     net = QuantumNetwork(edge_list=edge_list, max_per_edge=4, decoherence_time=6)
#     # net = QuantumNetwork(length_network=3, width_network=3, edge_length_km=1, max_per_edge=4, decoherence_time=6)
#     net.attempt_entanglement("A", "B", p_op=0.9, gen_time=0)
#     net.attempt_entanglement("B", "C", p_op=0.9, gen_time=4)
#     net.show_network_status(current_time=5)
#     net.show_network_status(current_time=9)
