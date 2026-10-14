"""The Weave: Volume tracing turbulently distorted SDFs, converted from <https://www.shadertoy.com/view/W3SSRm>

Source: https://github.com/nwhetsell/isf-shaders/blob/HEAD/The%20Weave.fs
Author: chronos <https://www.shadertoy.com/user/chronos>
License: Prosperity-3.0.0
license_class: noncommercial
Imported verbatim from GLSL (nwhetsell/isf-shaders); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Uses LYGIA (Prosperity licence, noncommercial).
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Generator"
    ],
    "CREDIT": "chronos <https://www.shadertoy.com/user/chronos>",
    "DESCRIPTION": "Volume tracing turbulently distorted SDFs, converted from <https://www.shadertoy.com/view/W3SSRm>",
    "INPUTS": [
        {
            "NAME": "focal",
            "LABEL": "Focal length",
            "TYPE": "float",
            "DEFAULT": 2.25,
            "MAX": 100,
            "MIN": 0
        },
        {
            "NAME": "twist",
            "LABEL": "Twist",
            "TYPE": "float",
            "DEFAULT": 5,
            "MAX": 100,
            "MIN": 0
        },
        {
            "NAME": "resolution",
            "LABEL": "Resolution",
            "TYPE": "float",
            "DEFAULT": 10,
            "MAX": 100,
            "MIN": 0
        },
        {
            "NAME": "saturation",
            "LABEL": "Saturation",
            "TYPE": "float",
            "DEFAULT": 50,
            "MAX": 100,
            "MIN": 0
        }
    ],
    "ISFVSN": "2"
}*/
/*
contributors: Patricio Gonzalez Vivo
description: some useful math constants
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define EIGHTH_PI 0.39269908169
#define QTR_PI 0.78539816339
#define HALF_PI 1.5707963267948966192313216916398
#define PI 3.1415926535897932384626433832795
#define TWO_PI 6.2831853071795864769252867665590
#define TAU 6.2831853071795864769252867665590
#define INV_PI 0.31830988618379067153776752674503
#define INV_SQRT_TAU 0.39894228040143267793994605993439
#define SQRT_HALF_PI 1.25331413732
#define PHI 1.618033988749894848204586834
#define EPSILON 0.0000001
#define GOLDEN_RATIO 1.6180339887
#define GOLDEN_RATIO_CONJUGATE 0.61803398875
#define GOLDEN_ANGLE 2.39996323
#define DEG2RAD (PI / 180.0)
#define RAD2DEG (180.0 / PI)
/*
contributors: Patricio Gonzalez Vivo
description: returns a 2x2 rotation matrix
use: <mat2> rotate2d(<float> radians)
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_ROTATE2D 
mat2 rotate2d(const in float r){
    float c = cos(r);
    float s = sin(r);
    return mat2(c, s, -s, c);
}
/*
contributors: Patricio Gonzalez Vivo
description: 'Fix the aspect ratio of a space keeping things squared for you.'
use: <vec2> aspect(<vec2> st, <vec2> st_size)
examples:
    - https://raw.githubusercontent.com/patriciogonzalezvivo/lygia_examples/main/draw_shapes.frag
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_ASPECT 
vec2 aspect(vec2 st, vec2 s) {
    st.x = st.x * (s.x / s.y);
    return st;
}
/*
contributors: Patricio Gonzalez Vivo
description: "It center the coordinates from 0 to 1 to -1 to 1\nSo the center goes\
    \ from 0.5 to 0.0. \n"
use: <float|vec2|vec3> center(<float|vec2|vec3> st)
examples:
    - https://raw.githubusercontent.com/patriciogonzalezvivo/lygia_examples/main/draw_shapes.frag
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_CENTER 
float center(float x) { return x * 2.0 - 1.0; }
vec2 center(vec2 v) { return v * 2.0 - 1.0; }
vec3 center(vec3 v) { return v * 2.0 - 1.0; }
vec3 cmap(float x)
{
    return pow(0.5 + 0.5 * cos(PI * x + vec3(1, 2, 3)), vec3(2.5));
}
void main()
{
    vec2 uv = aspect(center(gl_FragCoord.xy / RENDERSIZE), RENDERSIZE);
    vec3 ro = vec3(0, 0, TIME);
    vec3 rd = normalize(vec3(uv, -focal));
    vec3 color = vec3(0);
    float t = 0.;
    for (int i = 0; i < 99; i++) {
        vec3 p = t * rd + ro;
        float T = (t + TIME) / twist;
        p.xy = rotate2d(T) * p.xy;
        for (float f = 0.; f < 9.; f++) {
            float a = exp(f) / exp2(f);
            p += cos(p.yzx * a + TIME) / a;
        }
        float d = 1. / saturation + abs((ro - p - vec3(0, 1, 0)).y - 1.) / resolution;
        color += cmap(t) * 2e-3 / d;
        t += d;
    }
    color *= color * color;
    color = 1. - exp(-color);
    color = pow(color, vec3(1. / 2.2));
    gl_FragColor = vec4(color, 1);
}
'''


class IsfTheWeave(GlslSource):
    name = 'isf_the_weave'
    label = 'The Weave'
    description = 'Volume tracing turbulently distorted SDFs, converted from <https://www.shadertoy.com/view/W3SSRm>'
    dialect = 'isf'
    GLSL = GLSL
    params = scene_params({
        "focal": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
            "default": 2.25,
            "x-ui": {
                "label": "Focal length",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "focal"
            }
        },
        "twist": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
            "default": 5.0,
            "x-ui": {
                "label": "Twist",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "twist"
            }
        },
        "resolution": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
            "default": 10.0,
            "x-ui": {
                "label": "Resolution",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "resolution"
            }
        },
        "saturation": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
            "default": 50.0,
            "x-ui": {
                "label": "Saturation",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "saturation"
            }
        }
    }, render_scale=0.25)
