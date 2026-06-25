"""
Quantum Channel is the channel for transmitting qubit
    - length_km (float): the length of the quantum channel
    - p_e (float): the probability successfully generating an entanglement link over an edge
                   p_op * (1 - p_loss)
    -  p_op (float): the operation probability
    - loss_coef (float): fiber attenuation coefficient (dB/km)
    - p_loss (float): the probability of qubit loss in the channel

How to use:
    channel = QuantumChannel(u, v, length_km=length)
    channels[(u, v)] = channel

Quantum Node
    - node_id
    - memory: an instance of QuantumMemory
    - channels: a dictionary of outgoing QuantumChannel objects to other nodes

    Key Methods:
        - add_channel(self, node_id, peer_id)
        - get_channel(self, node_id, peer_id)
        - add_entanglement(self, peer_id, gen_time_slot)
        - delete_entanglement(self, current_time)
        - get_memory_usage(self)
        - show_node_status(self, current_time)
"""
import networkx as nx
from network_topology import Topology
from quantum_memory import QuantumMemory


class QuantumChannel:
    def __init__(self, node1, node2, length_km):
        self.node1 = node1
        self.node2 = node2
        self.length_km = length_km

    def show_channel_info(self):
        print(f"  Channel {(self.node1, self.node2)}, Length (km): {self.length_km}")


class QuantumNode:
    def __init__(self, node_id, max_per_edge=1, decoherence_time=10, max_total_memory=None):
        self.node_id = node_id
        self.memory = QuantumMemory(
            node_id=node_id,
            max_per_edge=max_per_edge,
            decoherence_time=decoherence_time,
            max_total=max_total_memory,
        )
        self.channels = {}  # key: (node_id, peer_id), value: QuantumChannel

    def add_channel(self, node_id, peer_id, length_km):
        self.channels[(node_id, peer_id)] = QuantumChannel(node_id, peer_id, length_km=length_km)

    def get_channel(self, node_id, peer_id):
        return self.channels.get((node_id, peer_id), None)

    # Called after entanglement link is successfully created, update the memory usage
    def node_record_entanglement(self, peer_id, link_id, gen_time_slot):
        return self.memory.occupy_memory(peer_id, link_id, gen_time=gen_time_slot)

    def node_delete_entanglement(self, current_time):
        self.memory.release_memory(current_time)

    def get_memory_usage(self):
        return self.memory.get_total_usage()

    # show the memory and the connected channel of the node
    def show_node_status(self, current_time):
        print(f"[Node {self.node_id}] Memory Content at [time slot {current_time}]")
        self.memory.show_memory(current_time)  # already released the memory

        print(f"[Node {self.node_id}] Channel Content at [time slot {current_time}]")
        for key, channel in self.channels.items():
            channel.show_channel_info()
        print('\n')

#
# if __name__ == "__main__":
#     # A - B - C - D
#     edge_list = [
#         ("A", "B", 10),
#         ("B", "C", 15),
#         ("C", "D", 20)
#     ]
#     topo = Topology(edge_list)
#     topo.show_topology()
#
#     nodeB = QuantumNode(node_id="B", max_per_edge=1, decoherence_time=5)
#     for u, v in topo.get_edges():
#         if u == "B" or v == "B":
#             length_km = topo.get_edge_length(u, v)
#             nodeB.add_channel(u, v, length_km)
#
#     nodeB.node_record_entanglement("A", 0, 1)
#     nodeB.node_record_entanglement("C", 5, 1)
#     nodeB.show_node_status(3)
#     nodeB.show_node_status(9)
