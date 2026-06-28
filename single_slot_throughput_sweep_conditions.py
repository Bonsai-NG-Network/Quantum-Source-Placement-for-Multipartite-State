"""
Configuration for single-slot multi-request throughput comparisons.

Edit this file to change default simulator parameters, compared algorithms,
plot ordering, or one-factor sweep conditions. The runner imports these values
directly, so the main script does not need separate default-parameter edits.

The default physical topology is a 5x5 grid for parameter debugging. Waxman
topology parameters are retained below and can be re-enabled after the grid
baseline produces stable throughput trends.
"""

DEFAULT_ALGORITHM_SPECS = (
    ("BT-SP_s_p", "BETWEENNESS", "singlepath_star_packing"),
    ("BT-MP_t_p", "BETWEENNESS", "multipath_tree_packing"),
    ("DP-SP_s_p", "DP", "singlepath_star_packing"),
    ("DP-MP_t_p", "DP", "multipath_tree_packing"),
    ("LP_R-SP_s_p", "LP_ROUND", "singlepath_star_packing"),
    ("LP_R-MP_t_p", "LP_ROUND", "multipath_tree_packing"),
    ("ILP_CG-SP_s_p", "ILP_CG", "singlepath_star_packing"),
    ("ILP_CG-MP_t_p", "ILP_CG", "multipath_tree_packing"),
    ("ILP-SP_s_p", "ILP", "singlepath_star_packing"),
    ("ILP-MP_t_p", "ILP", "multipath_tree_packing"),
)

SOURCE_ORDER = ["BT", "DP", "LP_R", "ILP_CG", "ILP"]
ROUTING_ORDER = ["SP_s_p", "MP_t_p"]

SOURCE_DISPLAY_LABELS = {
    "DP": "DP",
    "BT": "BT",
    "ILP": "ILP",
    "ILP_CG": "ILP-CG",
    "LP_R": "LP-R",
}

SOURCE_COLOR_PALETTE = {
    "BT": "#91BFDB",
    "DP": "#4575B4",
    "LP_R": "#FEE090",
    "ILP_CG": "#FC8D59",
    "ILP": "#D73027"
}

ROUTING_DISPLAY_LABELS = {
    "singlepath_star": "SP-s",
    "singlepath_star_packing": "SP-s-p",
    "multipath_tree": "MP-t",
    "multipath_tree_packing": "MP-t-p",
    "MP_t": "MP-t",
    "MP_t_p": "MP-t-p",
    "SP_s": "SP-s",
    "SP_s_p": "SP-s-p",
}

RANDOM_SEED = 6
NUM_TRIALS = 100
RUN_RUNTIME_PLOTS = True
SKIP_BUDGET_SWEEP = True
RUN_EXTRA_SWEEPS = True
NUM_USERS_PROTOCOLS_1 = 3
NUM_REQUESTS_PER_TRIAL = 4
OP_PROTOCOLS_1 = 0.9
Q_SWAP = 0.9
Q_FUS = 0.9
EDGE_CAPACITY = 4
NODE_MEMORY_CAPACITY = 12
TOPOLOGY_TYPE = "grid"
GRID_ROWS = 5
GRID_COLS = 5
GRID_EDGE_LENGTH_KM = 10.0
NETWORK_SCALE = 20
WAXMAN_DELTA = 0.8
WAXMAN_EPSILON = 0.01
WAXMAN_AREA_WIDTH_KM = 100.0
WAXMAN_AREA_HEIGHT_KM = 100.0
WAXMAN_ENSURE_CONNECTED = True
WAXMAN_MIN_LENGTH_KM = 1.0
WAXMAN_LENGTH_PRECISION = 2
FIXED_BUDGET = 50
SOURCE_BUDGETS = [40, 50, 60, 70, 80, 90, 100]
ILP_K_TREES = 32
LP_ROUND_K_TREES = 2
LP_ROUND_Z_THRESHOLD = 0.65
# Retained as legacy caller metadata; the Full ILP does not impose a
# per-request demand bound.
ILP_MAX_TREES_PER_REQUEST = ILP_K_TREES
ILP_CG_INITIAL_TREES = 1
ILP_CG_PRICING_TRIALS = 4
ILP_CG_MAX_TREES_PER_REQUEST = 2
ILP_CG_MAX_PRICING_COLUMNS_PER_REQUEST = 2
ILP_CG_MAX_ITERATIONS = 8
ILP_CG_USE_NESTED_POOL = True
ILP_CG_PRICING_MODE = "candidate_pool_exact"
ILP_CG_PRICING_POOL_TREES = ILP_K_TREES
# Active ILP objective is fixed in code as REPS-style source provisioning:
#   max sum_{r,t} rho_op[r,t] x_{r,t}
# Coverage priority, edge-redundancy rewards, and leftover-budget
# post-processing are disabled in the revised model.
ILP_OBJECTIVE_MODE = "reps_source_provisioning"
ENFORCE_THROUGHPUT_ORDER = False
THROUGHPUT_ORDER_EPSILON = 0.02
NO_DECOHERENCE_TIME = 10**12
SINGLE_SLOT_TIME = 1
DP_WEIGHT_TOPO = 0.0
DP_WEIGHT_DEMAND = 1.0
DP_WEIGHT_QUALITY = 0.0
DP_WEIGHT_OVERLAP = 0.0

OPERATION_PROBABILITIES = [0.6, 0.7, 0.8, 0.9, 1.0]
NUM_USERS_PER_REQUEST_VALUES = [3, 4, 5, 6]
QUANTUM_MEMORY_CAPACITIES = [8, 9, 10, 11, 12]
EDGE_CAPACITIES = [4, 5, 6, 7, 8]
NUM_REQUESTS_PER_TRIAL_VALUES = [3, 4, 5, 6, 7]
NETWORK_SCALES = [20, 40, 60, 80, 100]


DEFAULT_OPERATING_POINT = {
    "quantum_source_budget": FIXED_BUDGET,
    "operation_probability": OP_PROTOCOLS_1,
    "num_users_per_request": NUM_USERS_PROTOCOLS_1,
    "quantum_memory_capacity": NODE_MEMORY_CAPACITY,
    "edge_capacity": EDGE_CAPACITY,
    "num_requests_per_trial": NUM_REQUESTS_PER_TRIAL,
    # "network_scale": NETWORK_SCALE,
}


SWEEP_CONDITIONS = {
    "quantum_source_budget": SOURCE_BUDGETS,
    "operation_probability": OPERATION_PROBABILITIES,
    "num_users_per_request": NUM_USERS_PER_REQUEST_VALUES,
    "quantum_memory_capacity": QUANTUM_MEMORY_CAPACITIES,
    "edge_capacity": EDGE_CAPACITIES,
    "num_requests_per_trial": NUM_REQUESTS_PER_TRIAL_VALUES,
    # "network_scale": NETWORK_SCALES,
}


def one_factor_sweep_plan():
    """
    Return one-factor sweep settings around DEFAULT_OPERATING_POINT.

    Each item contains the varied parameter name, the tested values, and the
    fixed baseline values for all other parameters.
    """
    return [
        {
            "sweep_parameter": name,
            "sweep_values": values,
            "fixed_parameters": {
                key: value
                for key, value in DEFAULT_OPERATING_POINT.items()
                if key != name
            },
        }
        for name, values in SWEEP_CONDITIONS.items()
    ]
