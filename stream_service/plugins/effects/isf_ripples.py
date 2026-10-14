"""Ripples: Ripples (Distortion Effect).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Ripples.fs
Author: by carter rosenberg
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Distortion Effect"
    ],
    "CREDIT": "by carter rosenberg",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 1,
            "MAX": 32,
            "MIN": 0.1,
            "NAME": "level",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "MAX": 1,
            "MIN": 0,
            "NAME": "offset",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "MAX": 1,
            "MIN": 0,
            "NAME": "x_smear",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "MAX": 1,
            "MIN": 0.01,
            "NAME": "y_smear",
            "TYPE": "float"
        },
        {
            "DEFAULT": [
                0.5,
                0.5
            ],
            "MAX": [
                1,
                1
            ],
            "MIN": [
                0,
                0
            ],
            "NAME": "center",
            "TYPE": "point2D"
        },
        {
            "DEFAULT": 0,
            "LABELS": [
                "Single",
                "Double"
            ],
            "NAME": "mode",
            "TYPE": "long",
            "VALUES": [
                0,
                1
            ]
        }
    ],
    "ISFVSN": "2"
}
*/

const float pi = 3.14159265359;

#ifndef GL_ES
float distance (vec2 inCenter, vec2 pt)
{
	float tmp = pow(inCenter.x-pt.x,2.0)+pow(inCenter.y-pt.y,2.0);
	return pow(tmp,0.5);
}
#endif

void main() {
	vec2 uv = vec2(isf_FragNormCoord[0],isf_FragNormCoord[1]);
	vec2 texSize = RENDERSIZE;
	vec2 tc = uv * texSize;
	vec2 modifiedCenter = center * RENDERSIZE;
	float r = distance(modifiedCenter, tc);
	float a = atan ((tc.y-modifiedCenter.y),(tc.x-modifiedCenter.x));
	float radius = 1.0;
	float radius_sized = radius * length(RENDERSIZE);
	
	tc -= modifiedCenter;

	if (r < radius_sized) 	{
		float percent = 1.0-(radius_sized - r) / radius_sized;
		float adjustedOffset = offset * 2.0 * pi;
		if (mode == 0)	{
			tc.x = r*(1.0+sin(percent * level * 2.0 * pi + adjustedOffset))/2.0 * cos(a);
			tc.y = r*(1.0+sin(percent * level * 2.0 * pi + adjustedOffset))/2.0 * sin(a);
		}
		else if (mode == 1)	{
			tc.x = r*(1.0+sin(percent * level * 2.0 * pi * cos(adjustedOffset + percent * percent * level * 2.0 * pi)))/2.0 * cos(a);
			tc.y = r*(1.0+sin(percent * level * 2.0 * pi * cos(adjustedOffset + percent * percent * level * 2.0 * pi)))/2.0 * sin(a);
		}
		tc.x = mix(uv.x, tc.x, max(1.0-x_smear,0.001));
		tc.y = mix(uv.y, tc.y, max(1.0-y_smear,0.001));
	}
	tc += modifiedCenter;
	vec2 loc = tc / texSize;

	if ((loc.x < 0.0)||(loc.y < 0.0)||(loc.x > 1.0)||(loc.y > 1.0))	{
		gl_FragColor = vec4(0.0);
	}
	else	{
		gl_FragColor = IMG_NORM_PIXEL(inputImage, loc);
	}
}
'''


class IsfRipples(GlslEffect):
    name = 'isf_ripples'
    label = 'Ripples'
    description = 'Ripples (Distortion Effect).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    params = effect_params({
        "level": {
            "type": "number",
            "minimum": 0.1,
            "maximum": 32.0,
            "default": 1.0,
            "x-ui": {
                "label": "Level",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "level"
            }
        },
        "offset": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Offset",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "offset"
            }
        },
        "x_smear": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "X smear",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "x_smear"
            }
        },
        "y_smear": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Y smear",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "y_smear"
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
        "mode": {
            "type": "integer",
            "enum": [
                0,
                1
            ],
            "default": 0,
            "x-ui": {
                "label": "Mode",
                "widget": "select",
                "labels": [
                    "Single",
                    "Double"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "mode"
            }
        }
    })
