"""Chromatic Aberration: Radial lens-style chromatic aberration: red and blue channels are displaced along the line from a chromatic center.

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Chromatic%20Aberration.fs
Author: by VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Color Effect",
        "Distortion"
    ],
    "CREDIT": "by VIDVOX",
    "DESCRIPTION": "Radial lens-style chromatic aberration: red and blue channels are displaced along the line from a chromatic center.",
    "ISFVSN": "2",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0.02,
            "LABEL": "Amount",
            "MAX": 0.2,
            "MIN": 0.0,
            "NAME": "amount",
            "TYPE": "float"
        },
        {
            "DEFAULT": 2.0,
            "LABEL": "Falloff",
            "MAX": 4.0,
            "MIN": 1.0,
            "NAME": "falloff",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.0,
            "LABEL": "Red / Blue Balance",
            "MAX": 1.0,
            "MIN": -1.0,
            "NAME": "balance",
            "TYPE": "float"
        },
        {
            "DEFAULT": [
                0.5,
                0.5
            ],
            "LABEL": "Center",
            "MAX": [
                1.0,
                1.0
            ],
            "MIN": [
                0.0,
                0.0
            ],
            "NAME": "center",
            "TYPE": "point2D"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Direction",
            "LABELS": [
                "Outward (lens-style)",
                "Inward (inverted)"
            ],
            "NAME": "direction",
            "TYPE": "long",
            "VALUES": [
                0,
                1
            ]
        }
    ]
}*/


void main()	{
	vec2 uv         = isf_FragNormCoord;
	vec2 fromCenter = uv - center;
	
	//	Radius from chromatic center, normalized so the vertical edge midpoint
	//	sits at scale = 1; corners of wide-aspect frames intentionally exceed
	//	1.0, which matches how real lenses behave (more fringing in corners).
	float r     = length(fromCenter);
	float scale = pow(r * 2.0, falloff);
	
	//	Per-channel displacement.
	//	balance > 0  →  red moves more than blue
	//	balance < 0  →  blue moves more than red
	//	balance = 0  →  symmetric
	float redAmt  = amount * (1.0 + balance);
	float blueAmt = amount * (1.0 - balance);
	
	//	Outward (default): red samples farther from center, blue samples closer.
	//	Inward: swap, for a stylized "inverse lens" look.
	float dirSign = (direction == 0) ? 1.0 : -1.0;
	
	vec2 redOffset  = fromCenter * ( scale * redAmt  * dirSign);
	vec2 blueOffset = fromCenter * (-scale * blueAmt * dirSign);
	
	vec2 redUV   = uv + redOffset;
	vec2 greenUV = uv;
	vec2 blueUV  = uv + blueOffset;
	
	//	Three independent samples, one per channel.
	vec4 greenSample = IMG_NORM_PIXEL(inputImage, greenUV);
	float r_chan = IMG_NORM_PIXEL(inputImage, redUV ).r;
	float g_chan = greenSample.g;
	float b_chan = IMG_NORM_PIXEL(inputImage, blueUV).b;
	
	gl_FragColor = vec4(r_chan, g_chan, b_chan, greenSample.a);
}
'''


class IsfChromaticAberration(GlslEffect):
    name = 'isf_chromatic_aberration'
    label = 'Chromatic Aberration'
    description = 'Radial lens-style chromatic aberration: red and blue channels are displaced along the line from a chromatic center.'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    scale = 0.9
    params = effect_params({
        "p_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.2,
            "default": 0.02,
            "x-ui": {
                "label": "Amount",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "amount"
            }
        },
        "falloff": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 4.0,
            "default": 2.0,
            "x-ui": {
                "label": "Falloff",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "falloff"
            }
        },
        "balance": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Red / Blue Balance",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "balance"
            }
        },
        "center_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Center x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "center",
                "c": 0
            }
        },
        "center_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Center y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "center",
                "c": 1
            }
        },
        "direction": {
            "type": "integer",
            "enum": [
                0,
                1
            ],
            "default": 0,
            "x-ui": {
                "label": "Direction",
                "widget": "select",
                "labels": [
                    "Outward (lens-style)",
                    "Inward (inverted)"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "direction"
            }
        }
    })
