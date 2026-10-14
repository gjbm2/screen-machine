"""Vertical Close: The incoming image closes in from the left and right edges toward the middle

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/VerticalClose.glsl
Author: martiniti
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: martiniti
// License: MIT
// Description: The incoming image closes in from the left and right edges toward the middle
// Tags: wipe, split

vec4 transition (vec2 uv) {

  float s = 2.0 - abs((uv.x - 0.5) / (progress - 1.0)) - 2.0 * progress;
  
  return mix(
    getFromColor(uv),
    getToColor(uv),
    smoothstep(0.5, 0.0, s)
  );
}
'''


class GltVerticalClose(GlslTransition):
    name = 'glt_vertical_close'
    label = 'Vertical Close'
    description = 'The incoming image closes in from the left and right edges toward the middle'
    dialect = 'glt'
    GLSL = GLSL
    params = {}
