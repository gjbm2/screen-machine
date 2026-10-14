"""Mosaic transition: Both images break into mosaic blocks that blend, then sharpen into the incoming image

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/mosaic_transition.glsl
Author: YueDev
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: YueDev
// License: MIT
// Description: Both images break into mosaic blocks that blend, then sharpen into the incoming image
// Tags: mosaic, pixelate, blend

// @param Number of mosaic blocks
uniform float mosaicNum;// = 10.0

vec2 getMosaicUV(vec2 uv) {
  float mosaicWidth = 2.0 / mosaicNum * min(progress, 1.0 - progress);
  float mX = floor(uv.x / mosaicWidth) + 0.5;
  float mY = floor(uv.y / mosaicWidth) + 0.5;
  return vec2(mX * mosaicWidth, mY * mosaicWidth);
}

vec4 transition (vec2 uv) {
  vec2 mosaicUV = min(progress, 1.0 - progress) == 0.0 ? uv : getMosaicUV(uv);
  return mix(getFromColor(mosaicUV), getToColor(mosaicUV), progress * progress);
}
'''


class GltMosaicTransition(GlslTransition):
    name = 'glt_mosaic_transition'
    label = 'Mosaic transition'
    description = 'Both images break into mosaic blocks that blend, then sharpen into the incoming image'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "mosaic_num": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 20.0,
            "default": 10.0,
            "x-ui": {
                "label": "Number of mosaic blocks",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mosaicNum"
            }
        }
    }
