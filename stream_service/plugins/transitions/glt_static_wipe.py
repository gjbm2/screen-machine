"""Static wipe: A wipe from top to bottom with a band of TV static at its edge

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/static_wipe.glsl
Author: Ben Lucas
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: Ben Lucas
// License: MIT
// Description: A wipe from top to bottom with a band of TV static at its edge
// Tags: wipe, static, noise, vertical

#define PI 3.14159265359

float rnd (vec2 st) {
    return fract(sin(dot(st.xy,
                         vec2(10,70)))*
        12345.5453123);
}

// @param Wipe from top to bottom; false wipes from bottom to top
uniform bool u_transitionUpToDown; // = true
// @param Maximum height of the static band
uniform float u_max_static_span; // = 0.5

vec4 transition (vec2 uv) {
  

  float span = u_max_static_span*pow(sin(PI*progress),0.5);
  
  float transitionEdge = u_transitionUpToDown ? 1.0-uv.y : uv.y;
  float mixRatio = 1.0 - step(progress, transitionEdge);

  vec4 transitionMix = mix(
    getFromColor(uv),
    getToColor(uv),
    mixRatio
  );
  
  float noiseEnvelope = smoothstep(progress-span, progress, transitionEdge) * (1.0 - smoothstep(progress, progress + span, transitionEdge));
  vec4 noise = vec4(vec3(rnd(uv*(1.0+progress))), 1.0);
  

  return mix(transitionMix, noise, noiseEnvelope);
}
'''


class GltStaticWipe(GlslTransition):
    name = 'glt_static_wipe'
    label = 'Static wipe'
    description = 'A wipe from top to bottom with a band of TV static at its edge'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "u_transition_up_to_down": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Wipe from top to bottom; False wipes..."
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "u_transitionUpToDown"
            }
        },
        "u_max_static_span": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Maximum height of the static band",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "u_max_static_span"
            }
        }
    }
