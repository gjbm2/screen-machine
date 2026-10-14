"""Ripple: Ripples spread from the center while crossfading

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/ripple.glsl
Author: gre
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: gre
// License: MIT
// Description: Ripples spread from the center while crossfading
// Tags: ripple, wave, blend
// @param Frequency of the ripples
uniform float amplitude; // = 100.0
// @param Speed of the ripples
uniform float speed; // = 50.0

vec4 transition (vec2 uv) {
  vec2 dir = uv - vec2(.5);
  float dist = length(dir);
  vec2 offset = dir * (sin(progress * dist * amplitude - progress * speed) + .5) / 30. * progress;
  return mix(
    getFromColor(uv + offset),
    getToColor(uv),
    smoothstep(0.2, 1.0, progress)
  );
}
'''


class GltRipple(GlslTransition):
    name = 'glt_ripple'
    label = 'Ripple'
    description = 'Ripples spread from the center while crossfading'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "amplitude": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 200.0,
            "default": 100.0,
            "x-ui": {
                "label": "Frequency of the ripples",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "amplitude"
            }
        },
        "p_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
            "default": 50.0,
            "x-ui": {
                "label": "Speed of the ripples",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "speed"
            }
        }
    }
