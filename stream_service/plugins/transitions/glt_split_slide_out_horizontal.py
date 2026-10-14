"""Split Slide Out Horizontal: The outgoing image slides out as two halves moving horizontally in opposite directions

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/splitSlideOutHorizontal.glsl
Author: OllyOllyOlly
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: OllyOllyOlly
// License: MIT
// Description: The outgoing image slides out as two halves moving horizontally in opposite directions
// Tags: slide, split, horizontal

// @param Swap the directions of the halves
uniform bool reverse; // = false

const vec2 boundMin = vec2(0.0, 0.0);
const vec2 boundMax = vec2(1.0, 1.0);

bool inBounds (vec2 p) {
  return all(lessThan(boundMin, p)) && all(lessThan(p, boundMax));
}

vec4 transition (vec2 uv) {
  float modifier = (uv.y > 0.5 ? 1.0 : -1.0) * (reverse ? -1.0 : 1.0) ;
  vec2 p = vec2(uv.x + (progress * modifier), uv.y);

  return inBounds(p) ? getFromColor(p) : getToColor(uv);
}
'''


class GltSplitSlideOutHorizontal(GlslTransition):
    name = 'glt_split_slide_out_horizontal'
    label = 'Split Slide Out Horizontal'
    description = 'The outgoing image slides out as two halves moving horizontally in opposite directions'
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
