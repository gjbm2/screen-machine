"""Directionalwipe: A soft-edged diagonal wipe

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/directionalwipe.glsl
Author: gre
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: gre
// License: MIT
// Description: A soft-edged diagonal wipe
// Tags: wipe, directional

// @param Direction of the wipe
uniform vec2 direction; // = vec2(1.0, -1.0)
// @param Softness of the edge
uniform float smoothness; // = 0.5
 
const vec2 center = vec2(0.5, 0.5);
 
vec4 transition (vec2 uv) {
  vec2 v = normalize(direction);
  v /= abs(v.x)+abs(v.y);
  float d = v.x * center.x + v.y * center.y;
  float m =
    (1.0-step(progress, 0.0)) * // there is something wrong with our formula that makes m not equals 0.0 with progress is 0.0
    (1.0 - smoothstep(-smoothness, 0.0, v.x * uv.x + v.y * uv.y - (d-0.5+progress*(1.+smoothness))));
  return mix(getFromColor(uv), getToColor(uv), m);
}
'''


class GltDirectionalwipe(GlslTransition):
    name = 'glt_directionalwipe'
    label = 'Directionalwipe'
    description = 'A soft-edged diagonal wipe'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "direction_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "Direction of the wipe x",
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
            "minimum": -2.0,
            "maximum": 2.0,
            "default": -1.0,
            "x-ui": {
                "label": "Direction of the wipe y",
                "step": 0.1,
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
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Softness of the edge",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "smoothness"
            }
        }
    }
