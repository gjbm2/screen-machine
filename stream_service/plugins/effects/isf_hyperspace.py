"""Hyperspace: Hyperspace (Distortion Effect).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Hyperspace.fs
Author: VIDVOX
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
    "CREDIT": "VIDVOX",
    "DESCRIPTION": "",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0.5,
            "MAX": 1,
            "MIN": 0,
            "NAME": "centerX",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "MAX": 2,
            "MIN": 0,
            "NAME": "scrollAmount",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "MAX": 1,
            "MIN": 0,
            "NAME": "rightScrollOffset",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.5,
            "IDENTITY": 1,
            "MAX": 1,
            "MIN": 0,
            "NAME": "midHeight",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1,
            "NAME": "seamless",
            "TYPE": "bool"
        }
    ],
    "ISFVSN": "2"
}
*/

void main()	{
	vec2		loc = isf_FragNormCoord.xy;
	if (centerX == 0.0)	{
		
	}
	else if (centerX == 1.0)	{
		
	}
	else if (loc.x < centerX)	{
		loc.x = loc.x / centerX;
		loc.y = mix(0.5 + (loc.y-0.5) / midHeight,loc.y,1.0-loc.x);
		loc.x = loc.x+scrollAmount;
		if (seamless)	{
			loc.x = ((loc.x <= 1.0)||(loc.x >= 2.0)) ? loc.x : 3.0 - loc.x;
		}
		loc.x = mod(loc.x,1.0);
	}
	else	{
		float		rightScroll = mod(rightScrollOffset+scrollAmount,2.0);
		loc.x = (1.0-loc.x) / (1.0-centerX);
		loc.y = mix(0.5 + (loc.y-0.5) / midHeight,loc.y,1.0-loc.x);
		loc.x = loc.x+rightScroll;
		if (seamless)	{
			loc.x = ((loc.x <= 1.0)||(loc.x >= 2.0)) ? loc.x : 3.0 - loc.x;
		}
		loc.x = mod(loc.x,1.0);
	}
	
	vec4		inputPixelColor = vec4(0.0);
	
	if ((loc.y >= 0.0)&&(loc.y <= 1.0))
		inputPixelColor = IMG_NORM_PIXEL(inputImage,loc);
	
	gl_FragColor = inputPixelColor;
}
'''


class IsfHyperspace(GlslEffect):
    name = 'isf_hyperspace'
    label = 'Hyperspace'
    description = 'Hyperspace (Distortion Effect).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    params = effect_params({
        "center_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Center X",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "centerX"
            }
        },
        "scroll_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.0,
            "x-ui": {
                "label": "Scroll Amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "scrollAmount"
            }
        },
        "right_scroll_offset": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Right Scroll Offset",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "rightScrollOffset"
            }
        },
        "mid_height": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Mid Height",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "midHeight"
            }
        },
        "seamless": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Seamless"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "seamless"
            }
        }
    })
