"""Squareswire: Squares grow along a diagonal sweep to reveal the incoming image

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/squareswire.glsl
Author: gre
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: gre
// License: MIT
// Description: Squares grow along a diagonal sweep to reveal the incoming image
// Tags: squares, grid, wipe, directional
 
// @param Number of squares (columns, rows)
uniform ivec2 squares;// = ivec2(10,10)
// @param Direction of the sweep
uniform vec2 direction;// = vec2(1.0, -0.5)
// @param Softness of the sweep
uniform float smoothness; // = 1.6

const vec2 center = vec2(0.5, 0.5);
vec4 transition (vec2 p) {
  vec2 v = normalize(direction);
  v /= abs(v.x)+abs(v.y);
  float d = v.x * center.x + v.y * center.y;
  float offset = smoothness;
  float pr = smoothstep(-offset, 0.0, v.x * p.x + v.y * p.y - (d-0.5+progress*(1.+offset)));
  vec2 squarep = fract(p*vec2(squares));
  vec2 squaremin = vec2(pr/2.0);
  vec2 squaremax = vec2(1.0 - pr/2.0);
  float a = (1.0 - step(progress, 0.0)) * step(squaremin.x, squarep.x) * step(squaremin.y, squarep.y) * step(squarep.x, squaremax.x) * step(squarep.y, squaremax.y);
  return mix(getFromColor(p), getToColor(p), a);
}
'''


class GltSquareswire(GlslTransition):
    name = 'glt_squareswire'
    label = 'Squareswire'
    description = 'Squares grow along a diagonal sweep to reveal the incoming image'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "squares_x": {
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
                "u": "squares",
                "c": 0
            }
        },
        "squares_y": {
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
                "u": "squares",
                "c": 1
            }
        },
        "direction_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "Direction of the sweep x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "direction",
                "c": 0
            }
        },
        "direction_y": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": -0.5,
            "x-ui": {
                "label": "Direction of the sweep y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "direction",
                "c": 1
            }
        },
        "smoothness": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 3.2,
            "default": 1.6,
            "x-ui": {
                "label": "Softness of the sweep",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "smoothness"
            }
        }
    }
