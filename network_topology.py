"""
Physical network topology utilities.

The simulator consumes a topology as an undirected graph whose edges carry a
``length_km`` attribute. The default constructor remains compatible with the
existing edge-list format:

    Topology([(u, v, length_km), ...])

For grid experiments, use ``Topology.from_grid(...)`` or
``Topology.generate_grid_edge_list(...)``. For TNSM-style random experiments,
use ``Topology.from_waxman(...)`` or ``Topology.generate_waxman_edge_list(...)``.
The Waxman model follows:

    P(u, v) = delta * exp(-d(u, v) / (epsilon * L))

where ``L`` is the maximum Euclidean node distance in the sampled area.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx


EdgeList = List[Tuple[Any, Any, float]]
PositionMap = Dict[Any, Tuple[float, float]]


class Topology:
    def __init__(
        self,
        edge_list: Sequence[Tuple[Any, Any, float]],
        node_positions: Optional[PositionMap] = None,
    ):
        self.graph = nx.Graph()
        self.node_positions: PositionMap = dict(node_positions or {})

        for node, pos in self.node_positions.items():
            x, y = pos
            self.graph.add_node(node, x=float(x), y=float(y), pos=(float(x), float(y)))

        for u, v, length_km in edge_list:
            self.graph.add_node(u)
            self.graph.add_node(v)
            self.graph.add_edge(u, v, length_km=float(length_km))

        for node in self.graph.nodes:
            if node in self.node_positions:
                continue
            data = self.graph.nodes[node]
            if "pos" in data:
                x, y = data["pos"]
                self.node_positions[node] = (float(x), float(y))

    @classmethod
    def from_grid(
        cls,
        rows: int = 5,
        cols: int = 5,
        length_km: float = 10.0,
    ) -> "Topology":
        edge_list, positions = cls.generate_grid_edge_list(
            rows=rows,
            cols=cols,
            length_km=length_km,
        )
        return cls(edge_list=edge_list, node_positions=positions)

    @staticmethod
    def generate_grid_edge_list(
        rows: int = 5,
        cols: int = 5,
        length_km: float = 10.0,
    ) -> Tuple[EdgeList, PositionMap]:
        if rows < 1 or cols < 1:
            raise ValueError("rows and cols must be >= 1.")
        if length_km <= 0.0:
            raise ValueError("length_km must be > 0.")

        edge_list: EdgeList = []
        positions: PositionMap = {}
        for row in range(rows):
            for col in range(cols):
                node = row * cols + col
                positions[node] = (float(col) * float(length_km), float(row) * float(length_km))
                if col < cols - 1:
                    edge_list.append((node, node + 1, float(length_km)))
                if row < rows - 1:
                    edge_list.append((node, node + cols, float(length_km)))
        return edge_list, positions

    @classmethod
    def from_waxman(
        cls,
        num_nodes: int = 20,
        delta: float = 0.5,
        epsilon: float = 0.1,
        area_width_km: float = 1500.0,
        area_height_km: float = 1500.0,
        seed: Optional[int] = None,
        ensure_connected: bool = True,
        min_length_km: float = 1.0,
        length_precision: int = 2,
        alpha: Optional[float] = None,
        beta: Optional[float] = None,
    ) -> "Topology":
        edge_list, positions = cls.generate_waxman_edge_list(
            num_nodes=num_nodes,
            delta=delta,
            epsilon=epsilon,
            area_width_km=area_width_km,
            area_height_km=area_height_km,
            seed=seed,
            ensure_connected=ensure_connected,
            min_length_km=min_length_km,
            length_precision=length_precision,
            alpha=alpha,
            beta=beta,
        )
        return cls(edge_list=edge_list, node_positions=positions)

    @staticmethod
    def generate_waxman_edge_list(
        num_nodes: int = 20,
        delta: float = 0.5,
        epsilon: float = 0.1,
        area_width_km: float = 1500.0,
        area_height_km: float = 1500.0,
        seed: Optional[int] = None,
        ensure_connected: bool = True,
        min_length_km: float = 1.0,
        length_precision: int = 2,
        alpha: Optional[float] = None,
        beta: Optional[float] = None,
    ) -> Tuple[EdgeList, PositionMap]:
        if alpha is not None:
            delta = float(alpha)
        if beta is not None:
            epsilon = float(beta)

        if num_nodes < 2:
            raise ValueError("num_nodes must be >= 2.")
        if delta <= 0.0 or delta > 1.0:
            raise ValueError("delta must be in (0, 1].")
        if epsilon <= 0.0:
            raise ValueError("epsilon must be > 0.")
        if area_width_km <= 0.0 or area_height_km <= 0.0:
            raise ValueError("area dimensions must be positive.")

        rng = random.Random(seed)
        positions: PositionMap = {
            node: (
                rng.uniform(0.0, area_width_km),
                rng.uniform(0.0, area_height_km),
            )
            for node in range(num_nodes)
        }

        pair_distances = {
            (u, v): Topology._euclidean_km(positions[u], positions[v])
            for u in range(num_nodes)
            for v in range(u + 1, num_nodes)
        }
        max_distance = max(pair_distances.values())
        if max_distance <= 0.0:
            raise ValueError("Sampled Waxman positions are degenerate.")

        graph = nx.Graph()
        graph.add_nodes_from(range(num_nodes))
        for (u, v), distance_km in pair_distances.items():
            probability = delta * math.exp(-distance_km / (epsilon * max_distance))
            if rng.random() <= probability:
                graph.add_edge(
                    u,
                    v,
                    length_km=Topology._format_length(distance_km, min_length_km, length_precision),
                )

        if ensure_connected:
            Topology._connect_components_by_nearest_edges(
                graph,
                positions,
                min_length_km=min_length_km,
                length_precision=length_precision,
            )

        edge_list = [
            (u, v, float(data["length_km"]))
            for u, v, data in sorted(graph.edges(data=True), key=lambda item: (item[0], item[1]))
        ]
        return edge_list, positions

    @staticmethod
    def _euclidean_km(pos_a: Tuple[float, float], pos_b: Tuple[float, float]) -> float:
        return math.hypot(pos_a[0] - pos_b[0], pos_a[1] - pos_b[1])

    @staticmethod
    def _format_length(distance_km: float, min_length_km: float, precision: int) -> float:
        return round(max(float(distance_km), float(min_length_km)), int(precision))

    @staticmethod
    def _connect_components_by_nearest_edges(
        graph: nx.Graph,
        positions: PositionMap,
        min_length_km: float,
        length_precision: int,
    ) -> None:
        while not nx.is_connected(graph):
            components = [set(component) for component in nx.connected_components(graph)]
            best_pair = None
            best_distance = float("inf")

            for idx, comp_a in enumerate(components):
                for comp_b in components[idx + 1:]:
                    for u in comp_a:
                        for v in comp_b:
                            distance = Topology._euclidean_km(positions[u], positions[v])
                            if distance < best_distance:
                                best_distance = distance
                                best_pair = (u, v)

            if best_pair is None:
                raise RuntimeError("Unable to connect Waxman topology components.")

            u, v = best_pair
            graph.add_edge(
                u,
                v,
                length_km=Topology._format_length(best_distance, min_length_km, length_precision),
            )

    def to_edge_list(self) -> EdgeList:
        return [
            (u, v, float(data["length_km"]))
            for u, v, data in self.graph.edges(data=True)
        ]

    def show_topology(self):
        print("Show the current topology:")
        print(f"  Nodes: {self.graph.nodes(data=True)}")
        print(f"  Edges: {self.graph.edges(data=True)}")
        print("")

    def get_edges(self):
        return list(self.graph.edges)

    def get_nodes(self):
        return list(self.graph.nodes)

    def get_edge_length(self, node1, node2):
        return self.graph.edges[node1, node2].get("length_km", None)

    def get_edge_data(self, node1, node2):
        return self.graph.get_edge_data(node1, node2, default=0)

    def get_neighbors(self, node_id):
        return list(self.graph.neighbors(node_id))

    def get_positions(self) -> PositionMap:
        if self.node_positions:
            return dict(self.node_positions)

        if all(isinstance(node, tuple) and len(node) == 2 for node in self.graph.nodes):
            return {node: (float(node[1]), -float(node[0])) for node in self.graph.nodes}

        layout = nx.spring_layout(self.graph, seed=1)
        return {node: (float(pos[0]), float(pos[1])) for node, pos in layout.items()}

    def draw_topology(
        self,
        output_path: Optional[str] = None,
        title: str = "Network Topology",
        show_edge_labels: bool = True,
        show: bool = False,
    ) -> Optional[str]:
        pos = self.get_positions()
        labels = {node: str(node) for node in self.graph.nodes()}

        fig, ax = plt.subplots(figsize=(7.2, 6.2))
        nx.draw_networkx_edges(self.graph, pos, ax=ax, edge_color="#8a8a8a", width=1.2, alpha=0.75)
        nx.draw_networkx_nodes(
            self.graph,
            pos,
            ax=ax,
            node_size=360,
            node_color="#78a6c8",
            edgecolors="#34556b",
            linewidths=0.9,
        )
        nx.draw_networkx_labels(self.graph, pos, ax=ax, labels=labels, font_size=8, font_color="black")

        if show_edge_labels:
            edge_labels = {
                (u, v): f"{data['length_km']:.1f}"
                for u, v, data in self.graph.edges(data=True)
            }
            nx.draw_networkx_edge_labels(
                self.graph,
                pos,
                edge_labels=edge_labels,
                font_size=6,
                rotate=False,
                ax=ax,
            )

        ax.set_title(title)
        ax.set_xlabel("x (km)")
        ax.set_ylabel("y (km)")
        ax.set_aspect("equal", adjustable="box")
        ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.35)
        ax.tick_params(left=True, bottom=True, labelleft=True, labelbottom=True)
        fig.tight_layout()

        saved_path = None
        if output_path:
            fig.savefig(output_path, dpi=300, bbox_inches="tight")
            saved_path = output_path
        if show:
            plt.show()
        plt.close(fig)
        return saved_path


def main() -> None:
    import single_slot_throughput_sweep_conditions as conditions
    from seed_utils import derive_seed

    topology_type = str(conditions.TOPOLOGY_TYPE).lower()
    if topology_type == "grid":
        topo = Topology.from_grid(
            rows=conditions.GRID_ROWS,
            cols=conditions.GRID_COLS,
            length_km=conditions.GRID_EDGE_LENGTH_KM,
        )
        assert topo.graph.number_of_nodes() == conditions.GRID_ROWS * conditions.GRID_COLS
        assert topo.graph.number_of_edges() == (
            conditions.GRID_ROWS * (conditions.GRID_COLS - 1)
            + conditions.GRID_COLS * (conditions.GRID_ROWS - 1)
        )
        assert nx.is_connected(topo.graph)
        output_path = "grid_topology_test.png"
        topo.draw_topology(
            output_path=output_path,
            title=f"{conditions.GRID_ROWS}x{conditions.GRID_COLS} Grid Topology Test",
            show_edge_labels=True,
            show=False,
        )
        print("Grid topology test:")
        print(f"  Number of nodes: {topo.graph.number_of_nodes()}")
        print(f"  Number of edges: {topo.graph.number_of_edges()}")
        print(f"  Edge length: {conditions.GRID_EDGE_LENGTH_KM} km")
        print(f"  Saved visualization to: {output_path}")
        return

    if topology_type == "waxman":
        topology_seed = derive_seed(conditions.RANDOM_SEED, "network_topology", "main")
        topo = Topology.from_waxman(
            num_nodes=conditions.NETWORK_SCALE,
            delta=conditions.WAXMAN_DELTA,
            epsilon=conditions.WAXMAN_EPSILON,
            area_width_km=conditions.WAXMAN_AREA_WIDTH_KM,
            area_height_km=conditions.WAXMAN_AREA_HEIGHT_KM,
            seed=topology_seed,
            ensure_connected=conditions.WAXMAN_ENSURE_CONNECTED,
        )

        assert topo.graph.number_of_nodes() == conditions.NETWORK_SCALE
        assert topo.graph.number_of_edges() >= conditions.NETWORK_SCALE - 1
        assert nx.is_connected(topo.graph)
        assert all(data["length_km"] > 0.0 for _, _, data in topo.graph.edges(data=True))
        average_edge_length = sum(
            data["length_km"] for _, _, data in topo.graph.edges(data=True)
        ) / topo.graph.number_of_edges()

        output_path = "waxman_topology_test.png"
        topo.draw_topology(
            output_path=output_path,
            title="Waxman Random Topology Test",
            show_edge_labels=True,
            show=False,
        )

        print("Waxman topology test:")
        print(f"  Connected: {nx.is_connected(topo.graph)}")
        print(f"  Number of nodes: {topo.graph.number_of_nodes()}")
        print(f"  Number of edges: {topo.graph.number_of_edges()}")
        print(f"  Average degree: {sum(dict(topo.graph.degree()).values()) / topo.graph.number_of_nodes():.2f}")
        print(f"  Master seed: {conditions.RANDOM_SEED}")
        print(f"  Topology seed: {topology_seed}")
        print(f"  Waxman delta: {conditions.WAXMAN_DELTA}")
        print(f"  Waxman epsilon: {conditions.WAXMAN_EPSILON}")
        print(f"  Average connected-node distance: {average_edge_length:.2f} km")
        print(f"  Saved visualization to: {output_path}")
        return

    raise ValueError(f"Unsupported TOPOLOGY_TYPE: {conditions.TOPOLOGY_TYPE}")


if __name__ == "__main__":
    main()
