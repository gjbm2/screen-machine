"""Pinwheel: A spinning pinwheel reveals the incoming image

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/pinwheel.glsl
Author: Mr Speaker
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: Mr Speaker
// License: MIT
// Description: A spinning pinwheel reveals the incoming image
// Tags: pinwheel, radial, rotate

// @param Rotation speed
uniform float speed; // = 2.0

vec4 transition(vec2 uv) {
  
  vec2 p = uv.xy / vec2(1.0).xy;
  
  float circPos = atan(p.y - 0.5, p.x - 0.5) + progress * speed;
  float modPos = mod(circPos, 3.1415 / 4.);
  float signed = sign(progress - modPos);
  
  return mix(getToColor(p), getFromColor(p), step(signed, 0.5));
  
}
'''


class GltPinwheel(GlslTransition):
    name = 'glt_pinwheel'
    label = 'Pinwheel'
    description = 'A spinning pinwheel reveals the incoming image'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "p_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 4.0,
            "default": 2.0,
            "x-ui": {
                "label": "Rotation speed",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "speed"
            }
        }
    }
