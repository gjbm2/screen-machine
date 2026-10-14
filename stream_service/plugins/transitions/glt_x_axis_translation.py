"""X axis translation: The outgoing image slides to the right, uncovering a crossfade to the incoming one

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/x_axis_translation.glsl
Author: lizhongjian
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: lizhongjian
// License: MIT
// Description: The outgoing image slides to the right, uncovering a crossfade to the incoming one
// Tags: slide, horizontal, blend

vec4 transition (vec2 uv) {
  vec2 newUV = uv;
  newUV.x -= progress;
  if(uv.x >= progress)
  {
    return getFromColor(newUV);
  }

  
  return mix(
    getFromColor(uv),
    getToColor(uv),
    progress
  );
}
'''


class GltXAxisTranslation(GlslTransition):
    name = 'glt_x_axis_translation'
    label = 'X axis translation'
    description = 'The outgoing image slides to the right, uncovering a crossfade to the incoming one'
    dialect = 'glt'
    GLSL = GLSL
    params = {}
