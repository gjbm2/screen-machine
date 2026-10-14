"""Palettes: Cosine based palettes, based on <https://www.shadertoy.com/view/ll2GD3>

Source: https://github.com/nwhetsell/isf-shaders/blob/HEAD/Palettes.fs
Author: Inigo Quilez <https://iquilezles.org>
License: Prosperity-3.0.0
license_class: noncommercial
Imported verbatim from GLSL (nwhetsell/isf-shaders); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Uses LYGIA (Prosperity licence, noncommercial).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Filter"
    ],
    "CREDIT": "Inigo Quilez <https://iquilezles.org>",
    "DESCRIPTION": "Cosine based palettes, based on <https://www.shadertoy.com/view/ll2GD3>",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "NAME": "usePredefinedPalette",
            "LABEL": "Use predefined palette",
            "TYPE": "bool",
            "DEFAULT": true
        },
        {
            "NAME": "id",
            "LABEL": "Predefined palette",
            "TYPE": "long",
            "DEFAULT" : 0,
            "VALUES": [0, 1, 2, 3, 4, 5, 6],
            "LABELS" : [
                "Rainbow",
                "Dusk",
                "Rhododendron",
                "Green Earth",
                "Brown Earth",
                "Neon",
                "Watermelon"
            ]
        },
        {
            "NAME": "a",
            "LABEL": "Color a",
            "TYPE": "color",
            "DEFAULT": [0.5, 0.5, 0.5, 1]
        },
        {
            "NAME": "b",
            "LABEL": "Color b",
            "TYPE": "color",
            "DEFAULT": [0.5, 0.5, 0.5, 1]
        },
        {
            "NAME": "c",
            "LABEL": "Color c",
            "TYPE": "color",
            "DEFAULT": [1, 1, 1, 1]
        },
        {
            "NAME": "d",
            "LABEL": "Color d",
            "TYPE": "color",
            "DEFAULT": [0, 0.33, 0.67, 1]
        }
    ],
    "ISFVSN": "2"
}*/
/*
contributor: nan
description: |
    Computes the luminance of the specified linear RGB color using the luminance coefficients from Rec. 709.
    Note, ThreeJS seems to inject this in all their shaders. Which could lead to issues
use: luminance(<vec3|vec4> color)
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_LUMINANCE 
float luminance(in vec3 linear) { return dot(linear, vec3(0.21250175, 0.71537574, 0.07212251)); }
float luminance(in vec4 linear) { return luminance( linear.rgb ); }
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
contributors: Inigo Quiles
description:  Procedural generation of color palette algorithm explained here http://www.iquilezles.org/www/articles/palettes/palettes.htm
use: <vec3|vec4> palette(<float> t, <vec3|vec4> a, <vec3|vec4> b, <vec3|vec4> c, <vec3|vec4> d)
*/
#define FNC_PALETTE 
vec3 palette (in float t, in vec3 a, in vec3 b, in vec3 c, in vec3 d) { return a + b * cos(TAU * ( c * t + d )); }
vec4 palette (in float t, in vec4 a, in vec4 b, in vec4 c, in vec4 d) { return a + b * cos(TAU * ( c * t + d )); }
void main()
{
    vec4 inputColor = IMG_THIS_PIXEL(inputImage);
    float t = luminance(inputColor);
    vec3 color;
    if (usePredefinedPalette) {
             if (id == 0) color = palette( t, vec3(0.5,0.5,0.5),vec3(0.5,0.5,0.5),vec3(1.0,1.0,1.0),vec3(0.0,0.33,0.67) );
        else if (id == 1) color = palette( t, vec3(0.5,0.5,0.5),vec3(0.5,0.5,0.5),vec3(1.0,1.0,1.0),vec3(0.0,0.10,0.20) );
        else if (id == 2) color = palette( t, vec3(0.5,0.5,0.5),vec3(0.5,0.5,0.5),vec3(1.0,1.0,1.0),vec3(0.3,0.20,0.20) );
        else if (id == 3) color = palette( t, vec3(0.5,0.5,0.5),vec3(0.5,0.5,0.5),vec3(1.0,1.0,0.5),vec3(0.8,0.90,0.30) );
        else if (id == 4) color = palette( t, vec3(0.5,0.5,0.5),vec3(0.5,0.5,0.5),vec3(1.0,0.7,0.4),vec3(0.0,0.15,0.20) );
        else if (id == 5) color = palette( t, vec3(0.5,0.5,0.5),vec3(0.5,0.5,0.5),vec3(2.0,1.0,0.0),vec3(0.5,0.20,0.25) );
        else if (id == 6) color = palette( t, vec3(0.8,0.5,0.4),vec3(0.2,0.4,0.2),vec3(2.0,1.0,1.0),vec3(0.0,0.25,0.25) );
    } else {
        color = palette(t, a.rgb, b.rgb, c.rgb, d.rgb);
    }
    gl_FragColor = vec4(color, inputColor.a);
}
'''


class IsfPalettes(GlslEffect):
    name = 'isf_palettes'
    label = 'Palettes'
    description = 'Cosine based palettes, based on <https://www.shadertoy.com/view/ll2GD3>'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'colour'
    params = effect_params({
        "use_predefined_palette": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Use predefined palette"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "usePredefinedPalette"
            }
        },
        "id": {
            "type": "integer",
            "enum": [
                0,
                1,
                2,
                3,
                4,
                5,
                6
            ],
            "default": 0,
            "x-ui": {
                "label": "Predefined palette",
                "widget": "select",
                "labels": [
                    "Rainbow",
                    "Dusk",
                    "Rhododendron",
                    "Green Earth",
                    "Brown Earth",
                    "Neon",
                    "Watermelon"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "id"
            }
        },
        "a": {
            "type": "string",
            "format": "color",
            "default": "#808080",
            "x-ui": {
                "label": "Color a",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "a",
                "color": 4,
                "alpha": 1.0
            }
        },
        "b": {
            "type": "string",
            "format": "color",
            "default": "#808080",
            "x-ui": {
                "label": "Color b",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "b",
                "color": 4,
                "alpha": 1.0
            }
        },
        "c": {
            "type": "string",
            "format": "color",
            "default": "#ffffff",
            "x-ui": {
                "label": "Color c",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "c",
                "color": 4,
                "alpha": 1.0
            }
        },
        "d": {
            "type": "string",
            "format": "color",
            "default": "#0054ab",
            "x-ui": {
                "label": "Color d",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "d",
                "color": 4,
                "alpha": 1.0
            }
        }
    })
