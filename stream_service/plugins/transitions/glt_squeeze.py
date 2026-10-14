"""Squeeze: The outgoing image is squeezed into a line with color separation, revealing the incoming one

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/squeeze.glsl
Author: gre
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: gre
// License: MIT
// Description: The outgoing image is squeezed into a line with color separation, revealing the incoming one
// Tags: squeeze, chromatic, vertical
 
// @param Amount of color separation
uniform float colorSeparation; // = 0.04
 
vec4 transition (vec2 uv) {
  float y = 0.5 + (uv.y-0.5) / (1.0-progress);
  if (y < 0.0 || y > 1.0) {
     return getToColor(uv);
  }
  else {
    vec2 fp = vec2(uv.x, y);
    vec2 off = progress * vec2(0.0, colorSeparation);
    vec4 c = getFromColor(fp);
    vec4 cn = getFromColor(fp - off);
    vec4 cp = getFromColor(fp + off);
    return vec4(cn.r, c.g, cp.b, c.a);
  }
}
'''


class GltSqueeze(GlslTransition):
    name = 'glt_squeeze'
    label = 'Squeeze'
    description = 'The outgoing image is squeezed into a line with color separation, revealing the incoming one'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "color_separation": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.04,
            "x-ui": {
                "label": "Amount of color separation",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "colorSeparation"
            }
        }
    }
