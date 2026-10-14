"""Rotate scale fade: The outgoing image spins and zooms while fading to the incoming one, over a background color

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/rotate_scale_fade.glsl
Author: Fernando Kuteken
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: Fernando Kuteken
// License: MIT
// Description: The outgoing image spins and zooms while fading to the incoming one, over a background color
// Tags: rotate, zoom, fade

#define PI 3.14159265359

// @param Center of the rotation
uniform vec2 center; // = vec2(0.5, 0.5)
// @param Number of turns
uniform float rotations; // = 1
// @param Maximum zoom
uniform float scale; // = 8
// @color Background color
uniform vec4 backColor; // = vec4(0.15, 0.15, 0.15, 1.0)

vec4 transition (vec2 uv) {
  
  vec2 difference = uv - center;
  vec2 dir = normalize(difference);
  float dist = length(difference);
  
  float angle = 2.0 * PI * rotations * progress;
  
  float c = cos(angle);
  float s = sin(angle);
  
  float currentScale = mix(scale, 1.0, 2.0 * abs(progress - 0.5));
  
  vec2 rotatedDir = vec2(dir.x  * c - dir.y * s, dir.x * s + dir.y * c);
  vec2 rotatedUv = center + rotatedDir * dist / currentScale;
  
  if (rotatedUv.x < 0.0 || rotatedUv.x > 1.0 ||
      rotatedUv.y < 0.0 || rotatedUv.y > 1.0)
    return backColor;
    
  return mix(getFromColor(rotatedUv), getToColor(rotatedUv), progress);
}
'''


class GltRotateScaleFade(GlslTransition):
    name = 'glt_rotate_scale_fade'
    label = 'Rotate scale fade'
    description = 'The outgoing image spins and zooms while fading to the incoming one, over a background color'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "center_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Center of the rotation x",
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
            "default": 0.5,
            "x-ui": {
                "label": "Center of the rotation y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "center",
                "c": 1
            }
        },
        "rotations": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "Number of turns",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "rotations"
            }
        },
        "scale": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 16.0,
            "default": 8.0,
            "x-ui": {
                "label": "Maximum zoom",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "scale"
            }
        },
        "back_color": {
            "type": "string",
            "format": "color",
            "default": "#262626",
            "x-ui": {
                "label": "Background color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "backColor",
                "color": 4,
                "alpha": 1.0
            }
        }
    }
