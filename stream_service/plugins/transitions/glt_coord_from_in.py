"""Coord from in: A crossfade in which the colors of one image displace the other

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/coord-from-in.glsl
Author: haiyoucuv
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: haiyoucuv
// License: MIT
// Description: A crossfade in which the colors of one image displace the other
// Tags: displace, distort, blend

vec4 transition (vec2 uv) {

  vec4 coordTo = getToColor(uv);
  vec4 coordFrom = getFromColor(uv);

  return mix(
    getFromColor(mix(uv, coordTo.rg, progress)),
    getToColor(mix(coordFrom.rg, uv, progress)),
    progress
  );

}
'''


class GltCoordFromIn(GlslTransition):
    name = 'glt_coord_from_in'
    label = 'Coord from in'
    description = 'A crossfade in which the colors of one image displace the other'
    dialect = 'glt'
    GLSL = GLSL
    params = {}
