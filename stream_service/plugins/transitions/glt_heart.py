"""Heart: A heart shape grows from the center to reveal the incoming image

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/heart.glsl
Author: gre
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: gre
// License: MIT
// Description: A heart shape grows from the center to reveal the incoming image
// Tags: shape, wipe, heart

float inHeart (vec2 p, vec2 center, float size) {
  if (size==0.0) return 0.0;
  vec2 o = (p-center)/(1.6*size);
  float a = o.x*o.x+o.y*o.y-0.3;
  return step(a*a*a, o.x*o.x*o.y*o.y*o.y);
}
vec4 transition (vec2 uv) {
  return mix(
    getFromColor(uv),
    getToColor(uv),
    inHeart(uv, vec2(0.5, 0.4), progress)
  );
}
'''


class GltHeart(GlslTransition):
    name = 'glt_heart'
    label = 'Heart'
    description = 'A heart shape grows from the center to reveal the incoming image'
    dialect = 'glt'
    GLSL = GLSL
    params = {}
