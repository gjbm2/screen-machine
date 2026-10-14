"""Angular: A wipe that sweeps around the center from a starting angle

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/angular.glsl
Author: Fernando Kuteken
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: Fernando Kuteken
// License: MIT
// Description: A wipe that sweeps around the center from a starting angle
// Tags: wipe, radial, clock

#define PI 3.141592653589

// @range(0, 360) Angle where the wipe starts, in degrees
uniform float startingAngle; // = 90

vec4 transition (vec2 uv) {
  
  float offset = startingAngle * PI / 180.0;
  float angle = atan(uv.y - 0.5, uv.x - 0.5) + offset;
  float normalizedAngle = (angle + PI) / (2.0 * PI);
  
  normalizedAngle = normalizedAngle - floor(normalizedAngle);

  return mix(
    getFromColor(uv),
    getToColor(uv),
    step(normalizedAngle, progress)
    );
}
'''


class GltAngular(GlslTransition):
    name = 'glt_angular'
    label = 'Angular'
    description = 'A wipe that sweeps around the center from a starting angle'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "starting_angle": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 360.0,
            "default": 90.0,
            "x-ui": {
                "label": "Angle where the wipe starts, in degrees",
                "step": 1.0,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "startingAngle"
            }
        }
    }
