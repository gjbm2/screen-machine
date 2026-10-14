"""Rotate Transition: The images spin around the center while crossfading

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/rotateTransition.glsl
Author: haiyoucuv
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: haiyoucuv
// License: MIT
// Description: The images spin around the center while crossfading
// Tags: rotate, spin, blend

#define PI 3.1415926

vec2 rotate2D(in vec2 uv, in float angle){
  
  return uv * mat2(cos(angle), -sin(angle), sin(angle), cos(angle));
}
vec4 transition (vec2 uv) {
  
  vec2 p = fract(rotate2D(uv - 0.5, progress * PI * 2.0) + 0.5);

  return mix(
    getFromColor(p),
    getToColor(p),
    progress
  );
}
'''


class GltRotateTransition(GlslTransition):
    name = 'glt_rotate_transition'
    label = 'Rotate Transition'
    description = 'The images spin around the center while crossfading'
    dialect = 'glt'
    GLSL = GLSL
    params = {}
