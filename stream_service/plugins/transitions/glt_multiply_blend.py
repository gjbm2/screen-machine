"""Multiply blend: A crossfade through the multiplied blend of both images

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/multiply_blend.glsl
Author: Fernando Kuteken
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: Fernando Kuteken
// License: MIT
// Description: A crossfade through the multiplied blend of both images
// Tags: blend, fade

vec4 blend(vec4 a, vec4 b) {
  return a * b;
}

vec4 transition (vec2 uv) {
  
  vec4 blended = blend(getFromColor(uv), getToColor(uv));
  
  if (progress < 0.5)
    return mix(getFromColor(uv), blended, 2.0 * progress);
  else
    return mix(blended, getToColor(uv), 2.0 * progress - 1.0);
}

'''


class GltMultiplyBlend(GlslTransition):
    name = 'glt_multiply_blend'
    label = 'Multiply blend'
    description = 'A crossfade through the multiplied blend of both images'
    dialect = 'glt'
    GLSL = GLSL
    params = {}
