"""Wipe Right: A hard-edged wipe from left to right

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/wipeRight.glsl
Author: Jake Nelson
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: Jake Nelson
// License: MIT
// Description: A hard-edged wipe from left to right
// Tags: wipe, horizontal

vec4 transition(vec2 uv) {
  vec2 p=uv.xy/vec2(1.0).xy;
  vec4 a=getFromColor(p);
  vec4 b=getToColor(p);
  return mix(a, b, step(0.0+p.x,progress));
}
'''


class GltWipeRight(GlslTransition):
    name = 'glt_wipe_right'
    label = 'Wipe Right'
    description = 'A hard-edged wipe from left to right'
    dialect = 'glt'
    GLSL = GLSL
    params = {}
