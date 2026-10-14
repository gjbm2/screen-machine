"""Pixel Shifter: Shifts pixels up and down

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Pixel%20Shifter.fs
Author: by VIDVOX
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
    "CREDIT": "by VIDVOX",
    "DESCRIPTION": "Shifts pixels up and down",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Horizontal Phase",
            "MAX": 1,
            "MIN": 0,
            "NAME": "hPhase",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1,
            "LABEL": "Horizontal Frequency",
            "MAX": 16,
            "MIN": -16,
            "NAME": "hFrequency",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Horizontal Random",
            "MAX": 1,
            "MIN": 0,
            "NAME": "hRandom",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Vertical Phase",
            "MAX": 1,
            "MIN": 0,
            "NAME": "vPhase",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Vertical Frequency",
            "MAX": 16,
            "MIN": -16,
            "NAME": "vFrequency",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Vertical Random",
            "MAX": 1,
            "MIN": 0,
            "NAME": "vRandom",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1,
            "LABEL": "Sinusoidal",
            "NAME": "doSin",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 1,
            "LABEL": "Mirror",
            "NAME": "mirror",
            "TYPE": "bool"
        }
    ],
    "ISFVSN": "2"
}
*/


float		PI_CONST = 3.14159265359;


float rand(vec2 co){
    return fract(sin(dot(co.xy ,vec2(12.9898,78.233))) * 43758.5453);
}


void main()
{
	//	start with the pixel
	vec2 loc = isf_FragNormCoord;
	float modVal = 1.0;
	
	if (mirror)
		modVal = 2.0;
	
	//	shift the loc.x by the frequency * loc.y + phase
	if (doSin)
		loc.x = mod(hRandom * rand(vec2(TIME * 0.127, loc.x)) + loc.x + sign(hFrequency) * 0.5 * (1.0+cos(2.0 * PI_CONST * (hPhase + hFrequency * loc.y))), modVal);
	else
		loc.x = mod(hRandom * rand(vec2(TIME * 0.129, loc.x)) + loc.x + hFrequency * loc.y + hPhase, modVal);

	//	shift the loc.y by the frequency * loc.x + phase
	if (doSin)
		loc.y = mod(vRandom * rand(vec2(TIME * 0.273, loc.y)) + loc.y + sign(vFrequency) * 0.5 * (1.0+cos(2.0 * PI_CONST * (vPhase + vFrequency * loc.x))), modVal);
	else
		loc.y = mod(vRandom * rand(vec2(TIME * 0.341, loc.y)) +loc.y + vFrequency * loc.x + vPhase, modVal);
		
	if (loc.x > 1.0)
		loc.x = 2.0 - loc.x;
		
	if (loc.y > 1.0)
		loc.y = 2.0 - loc.y;
	
	gl_FragColor = IMG_NORM_PIXEL(inputImage,loc);
}
'''


class IsfPixelShifter(GlslEffect):
    name = 'isf_pixel_shifter'
    label = 'Pixel Shifter'
    description = 'Shifts pixels up and down'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    params = effect_params({
        "h_phase": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Horizontal Phase",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "hPhase"
            }
        },
        "h_frequency": {
            "type": "number",
            "minimum": -16.0,
            "maximum": 16.0,
            "default": 1.0,
            "x-ui": {
                "label": "Horizontal Frequency",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "hFrequency"
            }
        },
        "h_random": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Horizontal Random",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "hRandom"
            }
        },
        "v_phase": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Vertical Phase",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "vPhase"
            }
        },
        "v_frequency": {
            "type": "number",
            "minimum": -16.0,
            "maximum": 16.0,
            "default": 0.0,
            "x-ui": {
                "label": "Vertical Frequency",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "vFrequency"
            }
        },
        "v_random": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Vertical Random",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "vRandom"
            }
        },
        "do_sin": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Sinusoidal"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "doSin"
            }
        },
        "mirror": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Mirror"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mirror"
            }
        }
    })
