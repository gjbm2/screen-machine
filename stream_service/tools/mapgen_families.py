"""Parameter families for tools/mapgen.py: what a param does, from its name, label, unit, type and tier.

classify(kind, name, prop) -> family id (kind: sources | effects | transitions).  Ordered rules: first match wins.  Name tokens dominate; the x-ui label
(lower-cased, often Spanish for ISF imports) is a secondary hint; type / enum / format / unit / tier decide the
structural families first.

The room's sound (17 Oct, show_service's features) has its own families, so the rules can give each the signal that
moves it: shake_displacement (bass hits), sway_amplitude (groove), sparkle_shimmer (highs), grain_texture (highs,
density) and chroma_split (highs, punch); each is matched before the broad family that used to take it.
"""
import re

MACROS = {
    "sources": {"energy", "pulse", "speed", "intensity", "render_scale", "seed", "palette", "palette_period",
                "drift_speed", "palette_mix"},
    "effects": {"amount", "pulse"},
    "transitions": {"duration_s", "easing"},
}
# the standard macros proper (palette trio is a "palette" family even when it is a macro)
MACRO_FAMILY = {"energy": "macro.energy", "pulse": "macro.pulse", "speed": "macro.speed",
                "intensity": "macro.intensity", "render_scale": "quality", "seed": "seed",
                "palette": "palette", "palette_period": "palette", "drift_speed": "colour_cycle_rate",
                "palette_mix": "palette", "amount": "macro.amount", "duration_s": "macro.duration",
                "easing": "macro.easing"}

ROT_UNITS = {"°/s", "deg/s", "rev/s", "turns/s", "rad/s", "turn/s"}
RATE_UNITS = {"/s", "Hz", "×/s", "waves/s"} | ROT_UNITS


def toks(name):
    return [t for t in re.split(r"[_\W]+", name.lower()) if t]


def has(ts, *words):
    return any(w in ts for w in words)


def rx(s, pat):
    return re.search(pat, s) is not None


def classify(kind, name, p):
    n = name.lower()
    ts = toks(n)
    ui = p.get("x-ui") or {}
    lab = (ui.get("label") or "").lower()
    unit = (ui.get("unit") or "").strip()
    tier = p.get("x-tier")
    typ = p.get("type")
    bind = p.get("x-bindable")
    nl = n + " | " + lab

    if name in MACROS.get(kind, ()):
        return MACRO_FAMILY[name]
    if n in {"palette", "palette_period", "palette_mix", "trail_palette", "colour_mix"} or (("enum" in p) and "palette" in n):
        return "palette"

    if n == "energy" or (n.endswith("_energy")):
        return "macro.energy"
    if n in {"start_time"}:
        return "seed"
    # ---- never / structural first ------------------------------------------------------------------------
    if tier == "quality" or n in {"raymarch_steps", "ao_samples", "quality", "sample_count", "octree_depth",
                                  "fps_throttle", "buff_quality", "taps", "aa", "max_update_size", "subpixel",
                                  "step_size", "step_size_factor", "render_scale", "resolution", "frame_rate",
                                  "num_colors" if "quality" in lab else "__"} or "quality" == lab:
        return "quality"
    if (rx(n, r"(^|_)(seed|randomi[sz]e\w*|random_seed|noise_seed|p_seed|r_seed)($|_)") or lab == "seed") \
            and typ != "boolean" and not rx(n, r"seed_(density|layout|symmetry)"):
        return "seed"
    if rx(nl, r"mouse|use_audio|audio_|beat_sync|use_time|enable_mouse|show_heightmap|show_mask|show_source|"
              r"debug|clean_buffer|(^|_)reset|auto_clear|use_alt_image|use_mask|input_image|show_light"):
        return "legacy_input" if not rx(n, r"mouse_(x|y)|mouse_control") else "position_xy"
    if typ == "boolean":
        return "toggle"
    if "enum" in p:
        return "mode_enum"
    if p.get("format") == "color" or ui.get("widget") == "color":
        if rx(n, r"bg|background|back_colou?r|dark|shadow|floor|paper|ground|backdrop|fog|sky|ambient|"
                 r"border|outline|bottom|base_dim|gap"):
            return "colour_background"
        return "colour_rgb"

    # ---- beat triggers --------------------------------------------------------------------------------------
    if bind == "trigger" or rx(n, r"kick|punch|strobe") or (rx(n, r"(^|_)flash($|_)|_flash$|^flash_") and kind != "transitions") \
            or rx(lab, r"\bkick\b|on the beat|^beat |per beat"):
        if kind == "transitions":
            return "glow_light"
        return "beat_trigger"

    # ---- the room's sound: shake (bass hits) ------------------------------------------------------------------
    if rx(n, r"shake|jolt|trauma|vibrat|tremble|judder|quake|max_shift|max_roll|step_bob|footstep|depth_kick|"
             r"fov_pump") and not rx(n, r"rate|speed|freq|hz|_vel$|velocity|period") and not rx(n, r"angle|^rot|direction") and unit not in RATE_UNITS:
        return "shake_displacement"

    # ---- timing (effects / transitions) -------------------------------------------------------------------
    if rx(n, r"duration|stagger|hold_time|fade_time|decay_ms|crossfade|(^|_)lag$|colour_lag|pause|delay|"
             r"fade_in|time_stretch|steps_per_beat|crossfade_window|window$|hold_share|frame_hold") or unit == "s":
        return "timing"

    # ---- colour (numeric) -----------------------------------------------------------------------------------
    if rx(n, r"hue_drift|colou?r_shift|color_anim|palette_drift|cycle_speed") and (rx(lab, r"speed|drift|cycl|/s") or unit in RATE_UNITS):
        return "colour_cycle_rate"
    if rx(nl, r"palette index|\(palette\)") or rx(n, r"(^|_)hue($|_)|hue_shift|hue_spread|palette_shift|colou?r_shift|"
                                                     r"colou?r_phase|palette_spread|palette_index|ember_hue|face_hue|"
                                                     r"per_wave_hue|channel_cycle|color_templates|colour_by|"
                                                     r"p_palette|disc_palette"):
        return "hue_palette_index"
    if rx(n, r"drift|cycle_speed|colou?r_anim|palette_time|hue_drift|colour_decay") and rx(nl, r"colou?r|hue|palette") \
            and not rx(n, r"camera|centre|center|roll|wander|amount"):
        return "colour_cycle_rate"
    if rx(n, r"saturation|vibran|vibrancy|colou?rfulness|greyscale|sat_level|keep_saturation|chroma_weight|"
             r"colou?r_intensity|color_power|colou?r_magnitude"):
        return "saturation"
    if rx(n, r"contrast|gamma|levels$|tone|knee|brightness_curve|power_curve|lift|black_thresh|posterize|"
             r"quantize|bit_depth|colour_bits|dither"):
        return "contrast_tone"
    if rx(n, r"tint|colori[sz]e|warmth|(^|_)cool$|redness|sepia|colou?r_mix|colour_smear|temperature|"
             r"metal_tint|blue_shift|water_tint|mirror_tint|trap_colour|source_colou?r_mix|duotone|"
             r"colou?r_heat|ink_paper|print_tone|paper_tone|petal_tone|^[rgb]_weight$|bias_[rgb]|intensity_[rgb]|"
             r"xor_(red|green|blue)"):
        return "tint_colour_mix"

    # ---- rates ----------------------------------------------------------------------------------------------
    is_rate = (bind == "rate" or unit in RATE_UNITS or rx(n, r"speed|(^|_)rate($|_)|velocity|anim|time_scale|"
               r"time_factor|_freq\d?$|tempo|update_rate|cycle$|^current$|^flow$|^wind$|^gust$|launch_rate|"
               r"glyph_rate|pen_rate|omega|spin_rate|fb_speed|^auto$"))
    if is_rate and not rx(n, r"frequency$|wavelength|spatial|transition_speed|params_transition|"
                              r"param_smoothness|movement_transition|_transition$|smoothness|recuperacion|sync_speed"):
        if rx(n, r"rot|spin|orbit|roll|twirl|turn|swirl|rose|tumble|omega|revolve|gate_rotation|kaleidoscope_speed") \
                or unit in ROT_UNITS:
            return "rotation_rate"
        if rx(n, r"zoom|dolly|repeat_zoom|zoomexp"):
            return "zoom_rate"
        if rx(n, r"fl(y|ight)|travel|glide|creep|procession|swim|scroll|tunnel_speed|movement|walk|camera|"
                 r"stream_speed|march|pour"):
            return "travel_rate"
        return "motion_rate"
    if rx(n, r"transition_speed|params_transition|param_smoothness|movement_transition|stream_speed_transition|"
             r"gate_animation_transition|rotation_smoothness|zoom_smoothness|transition_smoothness|"
             r"pattern_focus_transition|update_smoothing|audio_smoothness|smoothing$|damping$|rotation_damping|"
             r"color_damping|scale_twist_damping|adapt_rate|recovery|_transition$|smoothness|sync_speed|adapt_level|lowpass|"
             r"input_lambda|noise_removal"):
        return "internal_smoothing"

    # ---- light / camera / position ------------------------------------------------------------------------
    if rx(n, r"light_(theta|phi|angle|dir|position|x|y)|sun_(elevation|angle|height|x)|light_dir|"
             r"terminator_angle|inclination|elevation$|^elevation|light_distance") and n != "sun":
        return "light_direction"
    if rx(n, r"camera|^fov|fov_|focal|view_radius|view_scale|orbit_altitude|cloud_altitude|altitude|"
             r"camera_height|flythrough|vantage|^pitch$|^bank$|^yaw$|tilt3d|^dof|depth_of_field|aperture|"
             r"^focus|view_rotation|pull_back|perspective") or rx(lab, r"camera"):
        if rx(n, r"pan|tilt|pitch|bank|yaw|rot|angle|orbit$"):
            if n == "pitch" and unit not in {"°", "deg"}:
                return "scale_size"
            return "camera_angle"
        return "camera_distance"
    if rx(n, r"(center|centre|origin|position|pos|offset|shift|slide|pan|tilt|burst|star_center|ball|pole|"
             r"point|focus|peak_point|pivot|zoom_center|warp_center|target_movement|pen_loc|input_center|"
             r"emitter|light|sun|feedback_center|feedback_shift|fb_d|crop|slit_pos|tear_position|"
             r"scratch_position|horizon|wave_origin|location)(_?[xyz]|_pt_[xy])?$") \
            and rx(n, r"(_?[xyz]$|_pt_[xy]$|^pan|^tilt|horizon|_offset$|^offset$|location|slit_pos|tear_position|"
                       r"scratch_position|wave_origin|^shift$|^slide$|_shift$)"):
        return "position_xy"
    if rx(n, r"direction_[xy]|dir_[xy]|gravity_dir|smoke_direction|u_direction|wind_direction|stroke_direction|"
             r"jolt_direction|sensed_direction|scan_dir|flip_direction"):
        return "direction_angle"
    if rx(n, r"angle|^rotation$|rotation_[xyz]$|^rot_[xyz]$|^rotate$|rotate_angle|deg$|_deg_|direction|"
             r"orientation|heading|^axis$|axis_bias|axis_mix|rot_axis|^twist_[xyz]$|^roll$|^tilt$|"
             r"slice_plane|plane_angle|spin_axis|starting_angle|post_rotate|pre_rotate|result_rotation|"
             r"cell_rotation|grid_rotation|tile_rotation|mat_rotation|star_rotation|aperture_rotation|"
             r"disk_rotation|max_rotation|max_rotate|copy_turn|jolt_turn|turn_angle|turns$|rotations$|"
             r"rot_down|fold_angle") or unit in {"°", "deg", "rad", "turn", "turns"}:
        return "direction_angle"

    # ---- feedback / trails -----------------------------------------------------------------------------------
    if rx(n, r"feedback|^fb_|trail|decay|persist|echo|ghost|afterimage|motion_blur|history|smear|"
             r"mask_hold|mosh_hold|heat_hold|crack_hold|saliency_hold|residue|fade_rate|fade_amount|color_fade|"
             r"^fade$|tracers|frames$|motion_frames|self_amplification|blur_feedback|bias_to_feedback|"
             r"center_feedback|flow_persistence|keyframe_mix|tape_wobble|distfading"):
        return "feedback_trail"

    # ---- the room's sound: sparkle (highs) ------------------------------------------------------------------
    if rx(n, r"sparkle|glint|glitter|shimmer|twinkle|scintill|spark|speck|firefly|glimmer") \
            and not rx(n, r"rate|speed|freq|hz|_vel$|velocity|period") and not rx(n, r"colou?r|hue|tint|size|radius|density|count") and unit not in RATE_UNITS:
        return "sparkle_shimmer"

    # ---- triggers-ish / glow / light ---------------------------------------------------------------------------
    if rx(n, r"glow|bloom|halo|sheen|sparkle|glint|glitter|shimmer|rim|shafts|starburst|lightning|bolt|"
             r"emissive|radiance|halation|light_intensity|light$|^light|lighting|backlight|edge_light|"
             r"floor_light|specular|gloss|fresnel|flare|aurora|beam_glow|core_glow|tracery|stars?$|star_field|"
             r"city_lights|firefly|spark|ember|neon|streak|god|ray|flash|reflection|caustic|metallic|lake_reflection|"
             r"speck_colou?r|^sun$") and not rx(n, r"ray(march|_steps)"):
        return "glow_light"
    if rx(n, r"brightness|bright$|^bright|exposure|(^|_)gain($|_)|luminance|luminosity|intensity|"
             r"master|^light$|ambient|dim$|_dim$|^dim|dark_light|center_brightness|source_gain|"
             r"sig_gain|heat_gain|motion_gain|size_gain|flow_gain|backdrop|background_dim|"
             r"^background$|base_dim|source_dim|cap_dim|gap_dim|wall_dim|shade|shading|ao_|absorb"):
        return "brightness_exposure"

    # ---- the room's sound: grain and chroma (highs) -----------------------------------------------------------
    if rx(n, r"grain|dirt|static|speckle|film_noise|scratches|noise_(amount|level|strength|mix|opacity|gain)|^noise$") \
            and not rx(n, r"rate|speed|freq|hz|_vel$|velocity|period") and not rx(n, r"size|scale|seed|octave|detail|tip"):
        return "grain_texture"
    if rx(n, r"chroma|aberr|fringe|rgb_split|rgb_spread|rgb_offset|rgb_shift|dispersion|prism|colou?r_separation|"
             r"convergence") and not rx(n, r"rate|speed|freq|hz|_vel$|velocity|period") \
            and not rx(n, r"weight|saturat|key|subsampl"):
        return "chroma_split"

    # ---- atmosphere ------------------------------------------------------------------------------------------
    if rx(n, r"fog|haze|mist|atmos|smoke|cloud|turbid|volumetric|nebula|dust|snow|marine_snow|darkmatter|"
             r"vignette|heat_haze"):
        return "fog_atmosphere"

    # ---- glitch / noise ----------------------------------------------------------------------------------
    if rx(n, r"flicker|glitch|glit_|noise|grain|scanline|scan_line|interlace|rgb_split|rgb_spread|rgb_offset|chroma|"
             r"aberr|fringe|tear|mosh|static|dirt|scratch|xor|corrupt|dropout|packet|jitter|hsync|vsync|"
             r"analog|tape|crt|bleed|block|pixel|dct|signal_loss|n_noise|chip|scramble|separation|"
             r"color_separation|colour_separation|dispersion|convergence|interference|chaos|random|"
             r"row_|blanking|hum|refresh_band|sync|bit|corruption|crack|burn"):
        return "glitch_noise"

    # ---- symmetry / segments / counts ------------------------------------------------------------------------
    if rx(n, r"symmetr|segments?$|^sides$|n_fold|wedges|^arms$|mirror|kaleido(scope)?_(amount|segments)|"
             r"repetitions|repeats|repeat_amount|fold$|^folds|divisions|nest_count|facet_count|lens_count|"
             r"petals$|spokes|lobes|curve_a|ribs|strands|subdivi|rule$|^n$|_n$|w\d_n|^cols$|^rows$|columns|"
             r"^slices$|^slats$|^cells$|tile_count|squares_|grid_num|polyhedron$|faces$|ratio_set|"
             r"number_of_divisions|round_to_divisions|horizontal_hexagons|horizontal_bars|mosaic_num"):
        return "symmetry_count"

    # ---- iterations / detail (cost-bearing) ------------------------------------------------------------------
    if rx(n, r"iteration|octaves|max_steps|^steps$|fractal_detail|terrain_detail|^detail|detail_[xy]|"
             r"recursion|depth_layers|layer_count|^layers$|layering|fold_iterations|fractal_iterations|"
             r"twist_per_iter|bounces|ripple_detail|nest"):
        return "iterations_detail"

    # ---- density / count / complexity --------------------------------------------------------------------------
    if rx(n, r"density|count|complexity|number|num_|^num|amount_of|coverage|population|particles|"
             r"variety|variation|branch|stars|spawn|launch|lines$|rings$|waves$|_count|"
             r"blob|^balls|jelly|fountains|planets|projectors|siphonophores|kelp|mycelium|sulphur|"
             r"mushroom|building|window_density|lights_per|object|seeds?_density|confetti|^count|"
             r"drops$|bloom_count|ember_count|curve_count|wave_count|speck_count|ghost_count|"
             r"sphere_count|stream_count|vortex_count|bar_count|lens|^dots$|^grid$|grid_density|"
             r"node|filament_density|petal_density|seed_density|shaft_density|beam_density|"
             r"triangle_density|leak_density|spark_density|sign_density|glyph_density|hatch_density|"
             r"pattern_density|line_density|mushroom_density"):
        return "density_count"

    # ---- the room's sound: sway (groove) ----------------------------------------------------------------------
    if rx(n, r"sway|wobble|wander|breath|flutter|undulat|bob$|_bob_|float_amount|drift_amount|idle_sway|meander") \
            and not rx(n, r"rate|speed|freq|hz|_vel$|velocity|period"):
        return "sway_amplitude"

    if rx(n, r"julia|^c_re$|^c_im$|formuparam|fractal_(mod|offset|shape|scale)|inversion|sign_offset|^power$|"
             r"slice_plane|attractor|generator_[uv]|evolution|curve_amp|cohesion|coupling|mod_value|^p[0-9]$|"
             r"^shape_|_shape$|tube_vs_sheet|truncation|polyhedron|morph$|^morph_range"):
        return "style_morph"
    # ---- distortion / amplitude ------------------------------------------------------------------------------
    if rx(n, r"distort|warp|twist|displace|turbul|wobble|amplitude|amp[xy]$|strength|^amount$|_amount$|amt$|"
             r"bend|fold|swirl|vortex|vorticity|ripple|wave|sway|morph|perturb|chaos|jolt|shake|wander|"
             r"lurch|droop|bounce|squash|squeeze|stretch|curl|flow|gust|wind|force|gravity|refract|lens|"
             r"barrel|inflate|bulge|pinch|melt|drip|liquid|viscosity|diffusion|advection|divergence|"
             r"flutter|breath|pulsation|power$|^power|magnitude|max_offset|max_shift|max_roll|"
             r"intensity|curvature|relief|bevel|extrusion|height_variance|roughness|choppiness|sea_state|"
             r"elevation_amplitude|ridge|terrain|dune|crust|carve|erode|cohesion|coupling|attractor|"
             r"julia|c_re|c_im|formuparam|fractal_(mod|offset|shape)|inversion|sign_offset|evolution|"
             r"generator|slide$|scroll|offset_amount|fb_amount|zoom_effect|zoom_breath|repeat_zoom|"
             r"displacement|depth_kick|fov_pump|step_bob|surface_morph|curve_amp|boundary_warp"):
        return "distortion_amplitude"

    # ---- zoom / scale / size -----------------------------------------------------------------------------------
    if rx(n, r"zoom|scale|size|radius|(^|_)width($|_)|height$|length|^depth$|_depth$|depth_|distance|"
             r"spacing|gap|grout|reach|spread|extension|span|tunnel|thick|line_weight|weight$|^stroke|"
             r"wire|ink_width|margin|padding|border|edge_thickness|cell|tile|^grid_[xy]$|block|chunk|"
             r"magnif|frequency|wavelength|period$|repeat_length|^frequency|^length|globe|disc_size|"
             r"far_fade|near|fan_spread|view|field_scale|structure_scale|dims|aspect|^ratio$|^a$|^b$|"
             r"cylinder|floating|mass") or unit in {"px", "× height", "× half-height", "screens", "m", "tiles",
                                                      "× half width", "× half height", "× copy height", "%"}:
        if rx(n, r"[a-z]_weight$") and not rx(n, r"line_weight"):
            return "tint_colour_mix" if rx(n, r"^[rgba]_weight$") else "style_morph"
        if rx(lab, r"->|↔|→") and not rx(n, r"scale|size|radius|zoom|width|thick"):
            return "style_morph"
        if rx(n, r"thick|line_weight|line_width|ink_width|wire|stroke|edge_thickness|border|line_size|"
                 r"edge_width|outline_width"):
            return "line_width"
        return "scale_size"

    # ---- thresholds / softness / shape --------------------------------------------------------------------
    if rx(n, r"thresh|soft|smooth|feather|falloff|sharp|hard|edge|blur|focus|bias|key|luma|mask|clamp|"
             r"range|min|max|low$|high$|cutoff|mid_|limit|curve|shape|profile|style|look|mode|pattern|"
             r"^type$|kind|fill|^mix$|blend|_mix$|mix_|^mix|opacity|alpha|weight|share|ratio|"
             r"proportion|balance|dry_wet|wet|vs_|_vs_|^adjust$|^order$|^route$|layout|preset|"
             r"template|family|material|env|environment|style|render_style|display_mode"):
        if rx(n, r"thresh|soft|smooth|feather|falloff|sharp|hard|blur|focus|edge|luma|key|clamp|cutoff|"
                 r"min|max|low$|high$|limit|bias|range|mid_"):
            return "threshold_softness"
        return "style_morph"
    if rx(n, r"snap|overshoot|anticipation|jolt|tracking|vertical_roll|idle_(trauma|buzz)|chop|meander|tumble|"
             r"scatter|^spin$"):
        return "snap_character"
    if rx(n, r"spin_[xyz]$|^phase$|_phase$|^h_phase|^v_phase|map_phase|slide\d_[xy]|point_input|pos_rgb|pole_|"
             r"fbm_rotation|slice_w|^p[0-9]$"):
        return "phase_offset" if rx(n, r"phase|slice_w|^p[0-9]$") else "position_xy" if rx(n, r"slide|pos_|pole_|point_input") else "direction_angle"
    if rx(n, r"color_base_[rgb]|colou?r$|paper_colou?r|red_cyan"):
        return "tint_colour_mix"
    if rx(lab, r"->|↔|→|\.\.|·|\bvs\b") or rx(n, r"truncation|cooling|^sky$|silhouette|grating|overlay|interior|"
             r"barrier|cage|water$|seams|paper_texture|double_layer|corner_round|split_probability|arcs_per|"
             r"arch_pointiness|anisotropy|impasto|bezel|surround|frost|polarity|stock|matrix|metric|map_source|"
             r"strip_overlay|region|emitter|spatial|slide_axis|pivot|hatch_layers|q_level|level$"):
        return "style_morph"
    return "other"
