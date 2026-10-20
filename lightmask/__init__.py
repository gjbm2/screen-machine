"""lightmask: the one implementation of the light -> mask mapping.

Pure Python (needs only ``astral``), no Flask imports, no module state.
Importable from the repo root by Flask (``import lightmask``) and by
room_service. See lightmask/README.md.
"""
from .model import (  # noqa: F401
    REPO_ROOT,
    SOURCE_BLEND,
    SOURCE_FIXED,
    SOURCE_MASKOFF,
    SOURCE_OVERRIDE,
    SOURCE_SENSOR,
    SOURCE_SOLAR,
    ConfigPaths,
    ConfigWatcher,
    LightConfig,
    Overrides,
    SensorInput,
    SensorMapping,
    blend_masks,
    curve_intensity,
    destinations_for_group,
    find_destinations_file,
    flask_compute_mask,
    flask_mask_route,
    intensity_for,
    load_config,
    mask_for,
    mask_for_dest,
    merged_cfg,
    resolve_targets,
    sensors_for_dest,
    solar_mask,
)
from .filter import (  # noqa: F401
    FilterParams,
    FilterParamsWatcher,
    FilterState,
    LuxFilter,
    MaskGate,
    SampleResult,
    exceeds_deadband,
    load_filter_params,
)

__version__ = "1.0.0"
