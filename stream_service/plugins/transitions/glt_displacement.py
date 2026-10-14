"""Displacement: A crossfade in which a displacement map (an extra grayscale texture) warps both images

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/displacement.glsl
Author: Travis Fischer
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: Travis Fischer
// License: MIT
// Description: A crossfade in which a displacement map (an extra grayscale texture) warps both images
// Tags: displace, distort, blend
//
// Adapted from a Codrops article by Robin Delaporte
// https://tympanus.net/Development/DistortionHoverEffect

uniform sampler2D displacementMap;

// @param Strength of the displacement
uniform float strength; // = 0.5

vec4 transition (vec2 uv) {
  float displacement = texture2D(displacementMap, uv).r * strength;

  vec2 uvFrom = vec2(uv.x + progress * displacement, uv.y);
  vec2 uvTo = vec2(uv.x - (1.0 - progress) * displacement, uv.y);

  return mix(
    getFromColor(uvFrom),
    getToColor(uvTo),
    progress
  );
}
'''


class GltDisplacement(GlslTransition):
    name = 'glt_displacement'
    label = 'Displacement'
    description = 'A crossfade in which a displacement map (an extra grayscale texture) warps both images'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "strength": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Strength of the displacement",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "strength"
            }
        }
    }
