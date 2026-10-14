"""Polka Dots Curtain: The incoming image appears through growing polka dots

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/PolkaDotsCurtain.glsl
Author: bobylito
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: bobylito
// License: MIT
// Description: The incoming image appears through growing polka dots
// Tags: dots, pattern
const float SQRT_2 = 1.414213562373;
// @param Number of dots across the image
uniform float dots; // = 20.0
uniform vec2 center; // = vec2(0, 0)

vec4 transition(vec2 uv) {
  bool nextImage = distance(fract(uv * dots), vec2(0.5, 0.5)) < ( progress / distance(uv, center));
  return nextImage ? getToColor(uv) : getFromColor(uv);
}
'''


class GltPolkaDotsCurtain(GlslTransition):
    name = 'glt_polka_dots_curtain'
    label = 'Polka Dots Curtain'
    description = 'The incoming image appears through growing polka dots'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "dots": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 40.0,
            "default": 20.0,
            "x-ui": {
                "label": "Number of dots across the image",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "dots"
            }
        },
        "center_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Center x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "center",
                "c": 0
            }
        },
        "center_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Center y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "center",
                "c": 1
            }
        }
    }
