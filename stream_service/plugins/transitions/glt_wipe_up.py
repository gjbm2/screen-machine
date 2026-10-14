"""Wipe Up: A hard-edged wipe from bottom to top

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/wipeUp.glsl
Author: Jake Nelson
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: Jake Nelson
// License: MIT
// Description: A hard-edged wipe from bottom to top
// Tags: wipe, vertical

vec4 transition(vec2 uv) {
  vec2 p=uv.xy/vec2(1.0).xy;
  vec4 a=getFromColor(p);
  vec4 b=getToColor(p);
  return mix(a, b, step(0.0+p.y,progress));
}
'''


class GltWipeUp(GlslTransition):
    name = 'glt_wipe_up'
    label = 'Wipe Up'
    description = 'A hard-edged wipe from bottom to top'
    dialect = 'glt'
    GLSL = GLSL
    params = {}
