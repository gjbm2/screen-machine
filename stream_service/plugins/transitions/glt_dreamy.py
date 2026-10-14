"""Dreamy: A crossfade with a gentle wavy distortion

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/Dreamy.glsl
Author: mikolalysenko
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: mikolalysenko
// License: MIT
// Description: A crossfade with a gentle wavy distortion
// Tags: wave, blend

vec2 offset(float progress, float x, float theta) {
  float phase = progress*progress + progress + theta;
  float shifty = 0.03*progress*cos(10.0*(progress+x));
  return vec2(0, shifty);
}
vec4 transition(vec2 p) {
  return mix(getFromColor(p + offset(progress, p.x, 0.0)), getToColor(p + offset(1.0-progress, p.x, 3.14)), progress);
}
'''


class GltDreamy(GlslTransition):
    name = 'glt_dreamy'
    label = 'Dreamy'
    description = 'A crossfade with a gentle wavy distortion'
    dialect = 'glt'
    GLSL = GLSL
    params = {}
