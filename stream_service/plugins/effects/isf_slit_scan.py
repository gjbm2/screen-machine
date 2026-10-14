"""Slit Scan: Pixels update only if within range of the specified lines to create a slit scan style

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Slit%20Scan.fs
Author: by VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Glitch"
    ],
    "CREDIT": "by VIDVOX",
    "DESCRIPTION": "Pixels update only if within range of the specified lines to create a slit scan style",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 1,
            "MAX": 1.5,
            "MIN": 0,
            "NAME": "spacing",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.33,
            "MAX": 1,
            "MIN": 0,
            "NAME": "line_width",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.25,
            "MAX": 1,
            "MIN": -1,
            "NAME": "angle",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.5,
            "MAX": 1,
            "MIN": 0,
            "NAME": "shift",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2",
    "PASSES": [
        {
            "PERSISTENT": true,
            "TARGET": "bufferVariableNameA"
        }
    ]
}
*/


const float pi = 3.14159265359;


float pattern() {
	float s = sin(angle * pi);
	float c = cos(angle * pi);
	vec2 tex = isf_FragNormCoord * RENDERSIZE;
	float spaced = length(RENDERSIZE) * spacing;
	vec2 point = vec2( c * tex.x - s * tex.y, s * tex.x + c * tex.y ) * max(1.0/spaced,0.001);
	float d = point.y;
	float w = line_width;
	if (w > spacing)	{
		w = 0.99*spacing;	
	}
	return ( mod(d + shift*spacing + w * 0.5,spacing) );
}

void main()
{
	vec4	freshPixel = IMG_PIXEL(inputImage,gl_FragCoord.xy);
	//	If we're on the line, update, otherwise use the stale pixel
	vec4 	result = IMG_PIXEL(bufferVariableNameA,gl_FragCoord.xy);
	float pat = pattern();
	float w = line_width;
	if (w > spacing)	{
		w = 0.99*spacing;	
	}

	if ((pat > 0.0)&&(pat <= w))	{
		float percent = (1.0-abs(w-2.0*pat)/w);
		percent = clamp(percent,0.0,1.0);
		result = mix(result, freshPixel, percent);
		//result = vec4(percent,percent,percent,1.0);
	}
	gl_FragColor = result;
}
'''


class IsfSlitScan(GlslEffect):
    name = 'isf_slit_scan'
    label = 'Slit Scan'
    description = 'Pixels update only if within range of the specified lines to create a slit scan style'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.65
    params = effect_params({
        "spacing": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.5,
            "default": 1.0,
            "x-ui": {
                "label": "Spacing",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "spacing"
            }
        },
        "line_width": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.33,
            "x-ui": {
                "label": "Line width",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "line_width"
            }
        },
        "angle": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": 0.25,
            "x-ui": {
                "label": "Angle",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "angle"
            }
        },
        "shift": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Shift",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "shift"
            }
        }
    })
