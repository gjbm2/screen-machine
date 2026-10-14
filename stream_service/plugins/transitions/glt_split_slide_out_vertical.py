"""Split Slide Out Vertical: The outgoing image slides out as two halves moving vertically in opposite directions

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/splitSlideOutVertical.glsl
Author: OllyOllyOlly
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: OllyOllyOlly
// License: MIT
// Description: The outgoing image slides out as two halves moving vertically in opposite directions
// Tags: slide, split, vertical

// @param Swap the directions of the halves
uniform bool reverse; // = false

const vec2 boundMin = vec2(0.0, 0.0);
const vec2 boundMax = vec2(1.0, 1.0);

bool inBounds (vec2 p) {
  return all(lessThan(boundMin, p)) && all(lessThan(p, boundMax));
}

vec4 transition (vec2 uv) {
  float modifier = (uv.x > 0.5 ? 1.0 : -1.0) * (reverse ? -1.0 : 1.0) ;
  vec2 p = vec2(uv.x, uv.y + (progress * modifier));

  return inBounds(p) ? getFromColor(p) : getToColor(uv);
}
'''


class GltSplitSlideOutVertical(GlslTransition):
    name = 'glt_split_slide_out_vertical'
    label = 'Split Slide Out Vertical'
    description = 'The outgoing image slides out as two halves moving vertically in opposite directions'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "reverse": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Swap the directions of the halves"
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "reverse"
            }
        }
    }
