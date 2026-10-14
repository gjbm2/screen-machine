"""Interlace: Interlace (Glitch, Retro).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Interlace.fs
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
            "DEFAULT": 4,
            "LABEL": "Line Size",
            "MAX": 50,
            "MIN": 1,
            "NAME": "lineSize",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2",
    "PASSES": [
        {
            "DESCRIPTION": "this buffer stores the last frame's odd / even state",
            "HEIGHT": "1",
            "PERSISTENT": true,
            "TARGET": "lastRow",
            "WIDTH": "1"
        },
        {
            "PERSISTENT": true,
            "TARGET": "lastFrame"
        }
    ]
}
*/

void main()
{
	//	if this is the first pass, i'm going to read the position from the "lastRow" image, and write a new position based on this and the hold variables
	if (PASSINDEX == 0)	{
		vec4		srcPixel = IMG_PIXEL(lastRow,vec2(0.5));
		//	i'm only using the X and Y components, which are the X and Y offset (normalized) for the frame
		srcPixel.x = (srcPixel.x) > 0.5 ? 0.0 : 1.0;
		gl_FragColor = srcPixel;
	}
	//	else this isn't the first pass- read the position value from the buffer which stores it
	else	{
		vec4		lastRow = IMG_PIXEL(lastRow,vec2(0.5));
		vec2		pixelCoord = isf_FragNormCoord * RENDERSIZE;
		
		if (mod(floor(pixelCoord.y),2.0 * lineSize) < lineSize + lineSize * lastRow.x)
			gl_FragColor = IMG_NORM_PIXEL(inputImage,isf_FragNormCoord);
		else
			gl_FragColor = IMG_NORM_PIXEL(lastFrame,isf_FragNormCoord);
	}
}
'''


class IsfInterlace(GlslEffect):
    name = 'isf_interlace'
    label = 'Interlace'
    description = 'Interlace (Glitch, Retro).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.7
    params = effect_params({
        "line_size": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 50.0,
            "default": 4.0,
            "x-ui": {
                "label": "Line Size",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "lineSize"
            }
        }
    })
