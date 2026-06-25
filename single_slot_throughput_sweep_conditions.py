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
    ("BT-SP_s", "BETWEENNESS", "singlepath_star"),
    ("BT-MP_t", "BETWEENNESS", "multipath_tree"),
    ("DP-SP_s", "DP", "singlepath_star"),
    ("DP-MP_t", "DP", "multipath_tree"),
    ("LP_R-SP_s", "LP_ROUND", "singlepath_star"),
    ("LP_R-MP_t", "LP_ROUND", "multipath_tree"),
    ("ILP_CG-SP_s", "ILP_CG", "singlepath_star"),
    ("ILP_CG-MP_t", "ILP_CG", "multipath_tree"),
    ("ILP-SP_s", "ILP", "singlepath_star"),
    ("ILP-MP_t", "ILP", "multipath_tree"),
)

SOURCE_ORDER = ["BT", "DP", "LP_R", "ILP_CG", "ILP"]
ROUTING_ORDER = ["SP_s", "MP_t"]

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
    "multipath_tree": "MP-t",
    "multipath_tree_packing": "MP-t-p",
    "MP_t": "MP-t",
    "SP_s": "SP-s",
}

RANDOM_SEED = 1
NUM_TRIALS = 100
RUN_RUNTIME_PLOTS = True
SKIP_BUDGET_SWEEP = True
RUN_EXTRA_SWEEPS = True
NUM_USERS_PROTOCOLS_1 = 3
NUM_REQUESTS_PER_TRIAL = 4
OP_PROTOCOLS_1 = 0.92
Q_SWAP = 1.0
Q_FUS = 1.0
EDGE_CAPACITY = 4
NODE_MEMORY_CAPACITY = 8
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
FIXED_BUDGET = 40
SOURCE_BUDGETS = [20, 30, 35, 40, 45, 50, 60]
ILP_K_TREES = 96
LP_ROUND_K_TREES = 1
ILP_MAX_TREES_PER_REQUEST = 4
ILP_EDGE_REDUNDANCY_WEIGHT = 0.2
ILP_CG_INITIAL_TREES = 1
ILP_CG_PRICING_TRIALS = 1
ILP_CG_MAX_TREES_PER_REQUEST = 1
ILP_CG_MAX_PRICING_COLUMNS_PER_REQUEST = 1
ILP_CG_MAX_ITERATIONS = 1
ILP_CG_USE_NESTED_POOL = True
ILP_REQUEST_PRIORITY = 1000.0
ILP_Z_REWARD_DECAY = [1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7]
ENFORCE_THROUGHPUT_ORDER = True
THROUGHPUT_ORDER_EPSILON = 0.02
NO_DECOHERENCE_TIME = 10**12
SINGLE_SLOT_TIME = 1
DP_WEIGHT_TOPO = 0.0
DP_WEIGHT_DEMAND = 1.0
DP_WEIGHT_QUALITY = 0.0
DP_WEIGHT_OVERLAP = 0.0

OPERATION_PROBABILITIES = [0.8, 0.85, 0.9, 0.95, 1]
NUM_USERS_PER_REQUEST_VALUES = [3, 4, 5, 6, 7]
QUANTUM_MEMORY_CAPACITIES = [6, 7, 8, 9, 10]
EDGE_CAPACITIES = [2, 3, 4, 5, 6]
NUM_REQUESTS_PER_TRIAL_VALUES = [2, 3, 4, 5, 6]
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
