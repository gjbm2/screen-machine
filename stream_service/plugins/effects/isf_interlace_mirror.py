"""Interlace Mirror: Interlace Mirror (Glitch, Retro).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Interlace%20Mirror.fs
Author: by Carter Rosenberg
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Glitch",
        "Retro"
    ],
    "CREDIT": "by Carter Rosenberg",
    "DESCRIPTION": "",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 1,
            "LABEL": "Horizontal",
            "NAME": "horizontal",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Vertical",
            "NAME": "vertical",
            "TYPE": "bool"
        }
    ],
    "ISFVSN": "2"
}
*/

void main()
{
	vec2 pixelCoord = isf_FragNormCoord * RENDERSIZE;
	vec2 loc = pixelCoord;
	if (vertical)	{
		if (mod(pixelCoord.x,2.0)>1.0)	{
			loc.y = RENDERSIZE.y - pixelCoord.y;
		}
	}
	if (horizontal)	{
		if (mod(pixelCoord.y,2.0)>1.0)	{
			loc.x = RENDERSIZE.x - pixelCoord.x;
		}	
	}
	gl_FragColor = IMG_PIXEL(inputImage,loc);
}
'''


class IsfInterlaceMirror(GlslEffect):
    name = 'isf_interlace_mirror'
    label = 'Interlace Mirror'
    description = 'Interlace Mirror (Glitch, Retro).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    params = effect_params({
        "horizontal": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Horizontal"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "horizontal"
            }
        },
        "vertical": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Vertical"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "vertical"
            }
        }
    })
