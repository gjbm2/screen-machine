"""Vertical Open: The incoming image opens from a line in the middle toward the left and right edges

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/VerticalOpen.glsl
Author: martiniti
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: martiniti
// License: MIT
// Description: The incoming image opens from a line in the middle toward the left and right edges
// Tags: wipe, split

vec4 transition (vec2 uv) {

  float regress = 1.0 - progress;
  float s = 2.0 - abs((uv.x - 0.5) / (regress - 1.0)) - 2.0 * regress;
  
  return mix(
    getFromColor(uv),
    getToColor(uv),
    smoothstep(0.0, 0.5, s)
  );
}
'''


class GltVerticalOpen(GlslTransition):
    name = 'glt_vertical_open'
    label = 'Vertical Open'
    description = 'The incoming image opens from a line in the middle toward the left and right edges'
    dialect = 'glt'
    GLSL = GLSL
    params = {}
