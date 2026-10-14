"""Directional easing: Like Directional, with an eased movement

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/directional-easing.glsl
Author: Max Plotnikov
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: Max Plotnikov
// License: MIT
// Description: Like Directional, with an eased movement
// Tags: slide, push, directional

// @param Direction of the movement
uniform vec2 direction; // = vec2(0.0, 1.0)

vec4 transition (vec2 uv) {
  float easing = sqrt((2.0 - progress) * progress);
  vec2 p = uv + easing * sign(direction);
  vec2 f = fract(p);
  return mix(
    getToColor(f),
    getFromColor(f),
    step(0.0, p.y) * step(p.y, 1.0) * step(0.0, p.x) * step(p.x, 1.0)
  );
}
'''


class GltDirectionalEasing(GlslTransition):
    name = 'glt_directional_easing'
    label = 'Directional easing'
    description = 'Like Directional, with an eased movement'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "direction_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Direction of the movement x",
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
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "Direction of the movement y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "direction",
                "c": 1
            }
        }
    }
