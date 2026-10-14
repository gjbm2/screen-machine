"""Circleopen: A soft-edged circle opens from the center to reveal the incoming image

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/circleopen.glsl
Author: gre
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: gre
// License: MIT
// Description: A soft-edged circle opens from the center to reveal the incoming image
// Tags: circle, iris, wipe
// @param Softness of the circle's edge
uniform float smoothness; // = 0.3
// @param Open the circle; false closes it instead
uniform bool opening; // = true

const vec2 center = vec2(0.5, 0.5);
const float SQRT_2 = 1.414213562373;

vec4 transition (vec2 uv) {
  float x = opening ? progress : 1.-progress;
  float m = smoothstep(-smoothness, 0.0, SQRT_2*distance(center, uv) - x*(1.+smoothness));
  return mix(getFromColor(uv), getToColor(uv), opening ? 1.-m : m);
}
'''


class GltCircleopen(GlslTransition):
    name = 'glt_circleopen'
    label = 'Circleopen'
    description = 'A soft-edged circle opens from the center to reveal the incoming image'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "smoothness": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.3,
            "x-ui": {
                "label": "Softness of the circle's edge",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "smoothness"
            }
        },
        "opening": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Open the circle; False closes it instead"
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "opening"
            }
        }
    }
