"""Colorphase: A crossfade in which each color channel changes at a different time

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/colorphase.glsl
Author: gre
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: gre
// License: MIT
// Description: A crossfade in which each color channel changes at a different time
// Tags: fade, color

// Usage: fromStep and toStep must be in [0.0, 1.0] range 
// and all(fromStep) must be < all(toStep)

// @range(0, 1) When each channel (r, g, b, a) starts changing
uniform vec4 fromStep; // = vec4(0.0, 0.2, 0.4, 0.0)
// @range(0, 1) When each channel (r, g, b, a) finishes changing
uniform vec4 toStep; // = vec4(0.6, 0.8, 1.0, 1.0)

vec4 transition (vec2 uv) {
  vec4 a = getFromColor(uv);
  vec4 b = getToColor(uv);
  return mix(a, b, smoothstep(fromStep, toStep, vec4(progress)));
}
'''


class GltColorphase(GlslTransition):
    name = 'glt_colorphase'
    label = 'Colorphase'
    description = 'A crossfade in which each color channel changes at a different time'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "from_step_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "When each channel (r, g, b, a) starts... x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "fromStep",
                "c": 0
            }
        },
        "from_step_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.2,
            "x-ui": {
                "label": "When each channel (r, g, b, a) starts... y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "fromStep",
                "c": 1
            }
        },
        "from_step_z": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.4,
            "x-ui": {
                "label": "When each channel (r, g, b, a) starts... z",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "fromStep",
                "c": 2
            }
        },
        "from_step_w": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "When each channel (r, g, b, a) starts... w",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "fromStep",
                "c": 3
            }
        },
        "to_step_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.6,
            "x-ui": {
                "label": "When each channel (r, g, b, a) finishes... x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "toStep",
                "c": 0
            }
        },
        "to_step_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.8,
            "x-ui": {
                "label": "When each channel (r, g, b, a) finishes... y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "toStep",
                "c": 1
            }
        },
        "to_step_z": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "When each channel (r, g, b, a) finishes... z",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "toStep",
                "c": 2
            }
        },
        "to_step_w": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "When each channel (r, g, b, a) finishes... w",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "toStep",
                "c": 3
            }
        }
    }
