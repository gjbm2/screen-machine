"""Windowblinds: A crossfade through horizontal window blinds

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/windowblinds.glsl
Author: Fabien Benetou
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: Fabien Benetou
// License: MIT
// Description: A crossfade through horizontal window blinds
// Tags: blinds, stripes, blend

vec4 transition (vec2 uv) {
  float t = progress;
  
  if (mod(floor(uv.y*100.*progress),2.)==0.)
    t*=2.-.5;
  
  return mix(
    getFromColor(uv),
    getToColor(uv),
    mix(t, progress, smoothstep(0.8, 1.0, progress))
  );
}
'''


class GltWindowblinds(GlslTransition):
    name = 'glt_windowblinds'
    label = 'Windowblinds'
    description = 'A crossfade through horizontal window blinds'
    dialect = 'glt'
    GLSL = GLSL
    params = {}
