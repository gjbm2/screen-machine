"""Fadecolor: A fade through a solid color, black by default

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/fadecolor.glsl
Author: gre
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: gre
// License: MIT
// Description: A fade through a solid color, black by default
// Tags: fade, color
// @color Color to fade through
uniform vec3 color;// = vec3(0.0)
// @range(0, 1) How long the color shows in the middle
uniform float colorPhase; // = 0.4 ; // if 0.0, there is no black phase, if 0.9, the black phase is very important
vec4 transition (vec2 uv) {
  return mix(
    mix(vec4(color, 1.0), getFromColor(uv), smoothstep(1.0-colorPhase, 0.0, progress)),
    mix(vec4(color, 1.0), getToColor(uv), smoothstep(    colorPhase, 1.0, progress)),
    progress);
}
'''


class GltFadecolor(GlslTransition):
    name = 'glt_fadecolor'
    label = 'Fadecolor'
    description = 'A fade through a solid color, black by default'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "color": {
            "type": "string",
            "format": "color",
            "default": "#000000",
            "x-ui": {
                "label": "Color to fade through",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "color",
                "color": 3,
                "alpha": 1.0
            }
        },
        "color_phase": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.4,
            "x-ui": {
                "label": "How long the color shows in the middle",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "colorPhase"
            }
        }
    }
