"""Simple Zoom: Zooms into the outgoing image, then fades to the incoming one

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/SimpleZoom.glsl
Author: 0gust1
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: 0gust1
// License: MIT
// Description: Zooms into the outgoing image, then fades to the incoming one
// Tags: zoom, fade

// @range(0.2, 1) Part of the transition spent zooming
uniform float zoom_quickness; // = 0.8
vec2 zoom(vec2 uv, float amount) {
  return 0.5 + ((uv - 0.5) * (1.0-amount));	
}

vec4 transition (vec2 uv) {
  float nQuick = clamp(zoom_quickness,0.2,1.0);
  return mix(
    getFromColor(zoom(uv, smoothstep(0.0, nQuick, progress))),
    getToColor(uv),
   smoothstep(nQuick-0.2, 1.0, progress)
  );
}'''


class GltSimpleZoom(GlslTransition):
    name = 'glt_simple_zoom'
    label = 'Simple Zoom'
    description = 'Zooms into the outgoing image, then fades to the incoming one'
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
        }
    }
