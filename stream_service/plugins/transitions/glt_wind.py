"""Wind: A ragged left-to-right wipe, as if the image was blown away by the wind

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/wind.glsl
Author: gre
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: gre
// License: MIT
// Description: A ragged left-to-right wipe, as if the image was blown away by the wind
// Tags: wipe, horizontal, noise

// @range(0, 1) Width of the ragged edge
uniform float size; // = 0.2
// @param Wipe from right to left instead
uniform bool reversed; // = false

float rand (vec2 co) {
  return fract(sin(dot(co.xy ,vec2(12.9898,78.233))) * 43758.5453);
}

vec4 transition (vec2 uv) {
  float x = reversed ? 1. - uv.x : uv.x;
  float r = rand(vec2(0, uv.y));
  float m = smoothstep(0.0, -size, x*(1.0-size) + size*r - (progress * (1.0 + size)));
  return mix(
    getFromColor(uv),
    getToColor(uv),
    m
  );
}
'''


class GltWind(GlslTransition):
    name = 'glt_wind'
    label = 'Wind'
    description = 'A ragged left-to-right wipe, as if the image was blown away by the wind'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "size": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.2,
            "x-ui": {
                "label": "Width of the ragged edge",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "size"
            }
        },
        "reversed": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Wipe from right to left instead"
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "reversed"
            }
        }
    }
