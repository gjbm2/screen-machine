"""Advanced Mosaic: Both images break into large blocks that blend, then sharpen into the incoming image

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/AdvancedMosaic.glsl
Author: Sergey Kosarevsky
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: Sergey Kosarevsky
// License: MIT
// Description: Both images break into large blocks that blend, then sharpen into the incoming image
// Tags: pixelate, mosaic, blend
// Ported from https://gist.github.com/corporateshark/21d2fdd24c706952dc8c

uniform float pixelSize; // = 50.0

vec4 transition(vec2 uv) {
  float T = progress;
  float half_ = 0.5;
  float size = (T < half_) ? mix(1.0, pixelSize, T / half_) : mix(pixelSize, 1.0, (T - half_) / half_);
  float D = size * 0.005;
  // Remap UV to center the mosaic pattern
  vec2 UV = (uv - 0.5) / D;
  vec2 coord = clamp(D * (ceil(UV - 0.5)) + 0.5, 0.0, 1.0);
  vec4 C0 = getFromColor(coord);
  vec4 C1 = getToColor(coord);
  return mix(C0, C1, T);
}
'''


class GltAdvancedMosaic(GlslTransition):
    name = 'glt_advanced_mosaic'
    label = 'Advanced Mosaic'
    description = 'Both images break into large blocks that blend, then sharpen into the incoming image'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "pixel_size": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
            "default": 50.0,
            "x-ui": {
                "label": "Pixel Size",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "pixelSize"
            }
        }
    }
