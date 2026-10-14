"""Left Right: The outgoing image shrinks away to the left while the incoming one slides in from the right

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/LeftRight.glsl
Author: zhmy
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: zhmy
// License: MIT
// Description: The outgoing image shrinks away to the left while the incoming one slides in from the right
// Tags: slide, zoom, horizontal

const vec4 black = vec4(0.0, 0.0, 0.0, 1.0);
const vec2 boundMin = vec2(0.0, 0.0);
const vec2 boundMax = vec2(1.0, 1.0);

bool inBounds (vec2 p) {
    return all(lessThan(boundMin, p)) && all(lessThan(p, boundMax));
}

vec4 transition (vec2 uv) {
    vec2 spfr,spto = vec2(-1.);

    float size = mix(1.0, 3.0, progress*0.2);
    spto = (uv + vec2(-0.5,-0.5))*vec2(size,size)+vec2(0.5,0.5);
    spfr = (uv - vec2(1.-progress, 0.0));
    if(inBounds(spfr)){
        return getToColor(spfr);
    }else if(inBounds(spto)){
        return getFromColor(spto) * (1.0 - progress);
    } else{
        return black;
    }
}'''


class GltLeftRight(GlslTransition):
    name = 'glt_left_right'
    label = 'Left Right'
    description = 'The outgoing image shrinks away to the left while the incoming one slides in from the right'
    dialect = 'glt'
    GLSL = GLSL
    params = {}
