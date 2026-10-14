"""Edge Transition: Both images fade through black with their edges glowing

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/EdgeTransition.glsl
Author: Woohyun Kim
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: Woohyun Kim
// License: MIT
// Description: Both images fade through black with their edges glowing
// Tags: edges, fade

uniform float edge_thickness; // = 0.001
uniform float edge_brightness; // = 8.0

vec4 detectEdgeColor(vec3[9] c) {
  /* adjacent texel array for texel c[4]
    036
    147
    258
  */
  vec3 dx = 2.0 * abs(c[7]-c[1]) + abs(c[2] - c[6]) + abs(c[8] - c[0]);
	vec3 dy = 2.0 * abs(c[3]-c[5]) + abs(c[6] - c[8]) + abs(c[0] - c[2]);
  float delta = length(0.25 * (dx + dy) * 0.5);
	return vec4(clamp(edge_brightness * delta, 0.0, 1.0) * c[4], 1.0);
}

vec4 getFromEdgeColor(vec2 uv) {
	vec3 c[9];
	for (int i=0; i < 3; ++i) for (int j=0; j < 3; ++j)
	{
	  vec4 color = getFromColor(uv + edge_thickness * vec2(i-1,j-1));
    c[3*i + j] = color.rgb;
	}
	return detectEdgeColor(c);
}

vec4 getToEdgeColor(vec2 uv) {
	vec3 c[9];
	for (int i=0; i < 3; ++i) for (int j=0; j < 3; ++j)
	{
	  vec4 color = getToColor(uv + edge_thickness * vec2(i-1,j-1));
    c[3*i + j] = color.rgb;
	}
	return detectEdgeColor(c);
}

vec4 transition (vec2 uv) {
  vec4 start = mix(getFromColor(uv), getFromEdgeColor(uv), clamp(2.0 * progress, 0.0, 1.0));
  vec4 end = mix(getToEdgeColor(uv), getToColor(uv), clamp(2.0 * (progress - 0.5), 0.0, 1.0));
  return mix(
    start,
    end,
    progress
  );
}
'''


class GltEdgeTransition(GlslTransition):
    name = 'glt_edge_transition'
    label = 'Edge Transition'
    description = 'Both images fade through black with their edges glowing'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "edge_thickness": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.001,
            "x-ui": {
                "label": "Edge thickness",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "edge_thickness"
            }
        },
        "edge_brightness": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 16.0,
            "default": 8.0,
            "x-ui": {
                "label": "Edge brightness",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "edge_brightness"
            }
        }
    }
