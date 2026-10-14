"""GLSL compatibility layer: third-party GLSL shaders (gl-transitions, ISF, Shadertoy) as ordinary plug-ins.

translate.py turns GLSL into one CUDA translation unit on top of glsl_compat.cuh (vec/mat types, swizzles,
GLSL built-ins, samplers over our frames, derivatives via warp shuffles); runtime.py compiles and runs it;
plugins.py has the Transition / Source / Effect wrappers that generated plug-in files subclass.  Upstream
licence texts are in LICENSES/.  importer.py regenerates the plug-in files from upstream checkouts.
"""
from .plugins import GlslEffect, GlslSource, GlslTransition, effect_params, scene_params  # noqa: F401
