"""Block Dissolve: The incoming image appears in small random blocks

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/BlockDissolve.glsl
Author: nwoeanhinnogaehr
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: nwoeanhinnogaehr
// License: MIT
// Description: The incoming image appears in small random blocks
// Tags: dissolve, blocks, noise
// Ported from https://gist.github.com/nwoeanhinnogaehr/b93818de23d4511fde10

uniform float blocksize; // = 0.02

float rand(vec2 co) {
  return fract(sin(dot(co, vec2(12.9898, 78.233))) * 43758.5453);
}

vec4 transition(vec2 uv) {
  return mix(getFromColor(uv), getToColor(uv), step(rand(floor(uv / blocksize)), progress));
}
'''


class GltBlockDissolve(GlslTransition):
    name = 'glt_block_dissolve'
    label = 'Block Dissolve'
    description = 'The incoming image appears in small random blocks'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "blocksize": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.02,
            "x-ui": {
                "label": "Blocksize",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "blocksize"
            }
        }
    }
