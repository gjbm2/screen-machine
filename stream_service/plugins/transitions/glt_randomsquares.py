"""Randomsquares: The incoming image appears in random squares

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/randomsquares.glsl
Author: gre
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: gre
// License: MIT
// Description: The incoming image appears in random squares
// Tags: squares, grid, dissolve

// @param Number of squares (columns, rows)
uniform ivec2 size; // = ivec2(10, 10)
// @param Softness of each square's switch
uniform float smoothness; // = 0.5
 
float rand (vec2 co) {
  return fract(sin(dot(co.xy ,vec2(12.9898,78.233))) * 43758.5453);
}

vec4 transition(vec2 p) {
  float r = rand(floor(vec2(size) * p));
  float m = smoothstep(0.0, -smoothness, r - (progress * (1.0 + smoothness)));
  return mix(getFromColor(p), getToColor(p), m);
}
'''


class GltRandomsquares(GlslTransition):
    name = 'glt_randomsquares'
    label = 'Randomsquares'
    description = 'The incoming image appears in random squares'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "size_x": {
            "type": "integer",
            "minimum": 0,
            "maximum": 40,
            "default": 10,
            "x-ui": {
                "label": "Number of squares (columns, rows) x",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "size",
                "c": 0
            }
        },
        "size_y": {
            "type": "integer",
            "minimum": 0,
            "maximum": 40,
            "default": 10,
            "x-ui": {
                "label": "Number of squares (columns, rows) y",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "size",
                "c": 1
            }
        },
        "smoothness": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Softness of each square's switch",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "smoothness"
            }
        }
    }
