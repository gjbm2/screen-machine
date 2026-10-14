"""Cannabisleaf: A cannabis-leaf shape grows from the center to reveal the incoming image

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/cannabisleaf.glsl
Author: @Flexi23
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: @Flexi23
// License: MIT
// Description: A cannabis-leaf shape grows from the center to reveal the incoming image
// Tags: shape, wipe

// inspired by http://www.wolframalpha.com/input/?i=cannabis+curve

vec4 transition (vec2 uv) {
  if(progress == 0.0){
    return getFromColor(uv);
  }
  vec2 leaf_uv = (uv - vec2(0.5))/10./pow(progress,3.5);
	leaf_uv.y += 0.35;
	float r = 0.18;
	float o = atan(leaf_uv.y, leaf_uv.x);
  return mix(getFromColor(uv), getToColor(uv), 1.-step(1. - length(leaf_uv)+r*(1.+sin(o))*(1.+0.9 * cos(8.*o))*(1.+0.1*cos(24.*o))*(0.9+0.05*cos(200.*o)), 1.));
}
'''


class GltCannabisleaf(GlslTransition):
    name = 'glt_cannabisleaf'
    label = 'Cannabisleaf'
    description = 'A cannabis-leaf shape grows from the center to reveal the incoming image'
    dialect = 'glt'
    GLSL = GLSL
    params = {}
