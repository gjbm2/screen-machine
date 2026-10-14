"""Scale in: The incoming image scales in from the center while fading in

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/scale-in.glsl
Author: haiyoucuv
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: haiyoucuv
// License: MIT
// Description: The incoming image scales in from the center while fading in
// Tags: zoom, fade

vec4 scale(in vec2 uv){
    uv = 0.5 + (uv - 0.5) * progress;
    return getToColor(uv);
}

vec4 transition (vec2 uv) {
  return mix(
    getFromColor(uv),
    scale(uv),
    progress
  );
}
'''


class GltScaleIn(GlslTransition):
    name = 'glt_scale_in'
    label = 'Scale in'
    description = 'The incoming image scales in from the center while fading in'
    dialect = 'glt'
    GLSL = GLSL
    params = {}
