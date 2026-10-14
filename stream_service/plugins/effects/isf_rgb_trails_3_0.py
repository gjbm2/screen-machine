"""RGB Trails 3.0: a persistent buffer is used to maintain an image which is constantly updated. Similar to VVMotionBlur, but each channel has its own weight

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/RGB%20Trails%203.0.fs
Author: by zoidberg
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"CREDIT": "by zoidberg",
	"ISFVSN": "2",
	"DESCRIPTION": "a persistent buffer is used to maintain an image which is constantly updated.  Similar to VVMotionBlur, but each channel has its own weight",
	"CATEGORIES": [
		"Color Effect", "Blur"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "rWeight",
			"TYPE": "float"
		},
		{
			"NAME": "gWeight",
			"TYPE": "float"
		},
		{
			"NAME": "bWeight",
			"TYPE": "float"
		},
		{
			"NAME": "aWeight",
			"TYPE": "float",
			"DEFAULT": 0.0
		}
	],
	"PASSES": [
		{
			"TARGET":"accum",
			"PERSISTENT": true,
			"FLOAT": true
		},
		{
		
		}
	]
	
}*/

void main()
{
	if (PASSINDEX==0)	{
		vec4		freshPixel = IMG_THIS_PIXEL(inputImage);
		vec4		stalePixel = IMG_THIS_PIXEL(accum);
		gl_FragColor = vec4(mix(freshPixel.r,stalePixel.r,rWeight), mix(freshPixel.g,stalePixel.g,gWeight), mix(freshPixel.b,stalePixel.b,bWeight), mix(freshPixel.a,stalePixel.a,aWeight));
	}
	else
		gl_FragColor = IMG_THIS_PIXEL(accum);
}
'''


class IsfRgbTrails30(GlslEffect):
    name = 'isf_rgb_trails_3_0'
    label = 'RGB Trails 3.0'
    description = 'a persistent buffer is used to maintain an image which is constantly updated. Similar to VVMotionBlur, but each channel has its own weight'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'feedback'
    scale = 0.8
    params = effect_params({
        "r_weight": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "R Weight",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "rWeight"
            }
        },
        "g_weight": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "G Weight",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "gWeight"
            }
        },
        "b_weight": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "B Weight",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "bWeight"
            }
        },
        "a_weight": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "A Weight",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "aWeight"
            }
        }
    })
