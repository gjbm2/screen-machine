"""Polar function: A flower shape with several petals grows to reveal the incoming image

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/polar_function.glsl
Author: Fernando Kuteken
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: Fernando Kuteken
// License: MIT
// Description: A flower shape with several petals grows to reveal the incoming image
// Tags: shape, wipe, polar

#define PI 3.14159265359

// @param Number of petals
uniform int segments; // = 5

vec4 transition (vec2 uv) {
  
  float angle = atan(uv.y - 0.5, uv.x - 0.5) - 0.5 * PI;
  float normalized = (angle + 1.5 * PI) * (2.0 * PI);
  
  float radius = (cos(float(segments) * angle) + 4.0) / 4.0;
  float difference = length(uv - vec2(0.5, 0.5));
  
  if (difference > radius * progress)
    return getFromColor(uv);
  else
    return getToColor(uv);
}
'''


class GltPolarFunction(GlslTransition):
    name = 'glt_polar_function'
    label = 'Polar function'
    description = 'A flower shape with several petals grows to reveal the incoming image'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "segments": {
            "type": "integer",
            "minimum": 0,
            "maximum": 20,
            "default": 5,
            "x-ui": {
                "label": "Number of petals",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "segments"
            }
        }
    }
