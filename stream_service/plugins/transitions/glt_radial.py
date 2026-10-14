"""Radial: A radial wipe that sweeps around the center like a clock hand

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/Radial.glsl
Author: Xaychru
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// License: MIT
// Author: Xaychru
// Description: A radial wipe that sweeps around the center like a clock hand
// Tags: wipe, radial, clock
// ported by gre from https://gist.github.com/Xaychru/ce1d48f0ce00bb379750

uniform float smoothness; // = 1.0

const float PI = 3.141592653589;

vec4 transition(vec2 p) {
  vec2 rp = p*2.-1.;
  return mix(
    getToColor(p),
    getFromColor(p),
    smoothstep(0., smoothness, atan(rp.y,rp.x) - (progress-.5) * PI * 2.5)
  );
}
'''


class GltRadial(GlslTransition):
    name = 'glt_radial'
    label = 'Radial'
    description = 'A radial wipe that sweeps around the center like a clock hand'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "smoothness": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "Smoothness",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "smoothness"
            }
        }
    }
