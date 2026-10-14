"""Simple Zoom Out: The incoming image zooms out into place while fading in

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/SimpleZoomOut.glsl
Author: Tianshuo
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: Tianshuo
// License: MIT
// Description: The incoming image zooms out into place while fading in
// Tags: zoom, fade


// @range(0.2, 1) Part of the transition spent zooming
uniform float zoom_quickness; // = 0.8
// @param Fade instead of cutting to the incoming image
uniform bool fade; // = true
vec2 zoom(vec2 uv, float amount) {
  return 0.5 + ((uv - 0.5) * (1.0-amount));	
}

vec4 transition (vec2 uv) {
  float nQuick = clamp(zoom_quickness,0.2,1.0);
  return mix(
    getFromColor(uv),
    getToColor(zoom(uv,1.-smoothstep(1.-nQuick, 1., progress))),
   fade?smoothstep(1.0-nQuick, 1., progress):(progress<1.0-nQuick?0.0:1.0)
  );
}
'''


class GltSimpleZoomOut(GlslTransition):
    name = 'glt_simple_zoom_out'
    label = 'Simple Zoom Out'
    description = 'The incoming image zooms out into place while fading in'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "zoom_quickness": {
            "type": "number",
            "minimum": 0.2,
            "maximum": 1.0,
            "default": 0.8,
            "x-ui": {
                "label": "Part of the transition spent zooming",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "zoom_quickness"
            }
        },
        "fade": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Fade instead of cutting to the incoming..."
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "fade"
            }
        }
    }
