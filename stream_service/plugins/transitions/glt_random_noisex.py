"""Random Noisex: The incoming image appears pixel by pixel in random noise

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/randomNoisex.glsl
Author: towrabbit
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: towrabbit
// License: MIT
// Description: The incoming image appears pixel by pixel in random noise
// Tags: dissolve, noise

float random (vec2 st) {
    return fract(sin(dot(st.xy,vec2(12.9898,78.233)))*43758.5453123);
}
vec4 transition (vec2 uv) {
  vec4 leftSide = getFromColor(uv);
  vec4 rightSide = getToColor(uv);
  float uvz = floor(random(uv)+progress);
  return mix(leftSide,rightSide,uvz);
}
'''


class GltRandomNoisex(GlslTransition):
    name = 'glt_random_noisex'
    label = 'Random Noisex'
    description = 'The incoming image appears pixel by pixel in random noise'
    dialect = 'glt'
    GLSL = GLSL
    params = {}
