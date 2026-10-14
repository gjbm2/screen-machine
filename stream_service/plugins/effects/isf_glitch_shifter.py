"""Glitch Shifter: Glitch Shifter (Glitch).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Glitch%20Shifter.fs
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
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0.1,
            "LABEL": "Size",
            "MAX": 0.5,
            "MIN": 0,
            "NAME": "glitch_size",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.2,
            "LABEL": "Horizontal Amount",
            "MAX": 1,
            "MIN": 0,
            "NAME": "glitch_horizontal",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Vertical Amount",
            "MAX": 1,
            "MIN": 0,
            "NAME": "glitch_vertical",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1,
            "LABEL": "Randomize Size",
            "NAME": "randomize_size",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Randomize Zoom",
            "NAME": "randomize_zoom",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Use Alt Image",
            "NAME": "use_alt_image",
            "TYPE": "bool"
        },
        {
            "NAME": "altImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": [
                0,
                0
            ],
            "LABEL": "Offset",
            "MAX": [
                1,
                1
            ],
            "MIN": [
                0,
                0
            ],
            "NAME": "offset",
            "TYPE": "point2D"
        }
    ],
    "ISFVSN": "2"
}
*/

float rand(vec2 co){
    return fract(sin(dot(co.xy ,vec2(12.9898,78.233))) * 43758.5453);
}

void main()
{
	vec2 xy;
	vec4 returnMe;
	bool shifted = false;
	xy.x = isf_FragNormCoord[0];
	xy.y = isf_FragNormCoord[1];
	
	//	quantize the xy to the glitch_amount size
	//xy = floor(xy / glitch_size) * glitch_size;
	vec2 random;

	float local_glitch_size = glitch_size;
	float random_offset = 0.0;
	
	if (randomize_size)	{
		random_offset = mod(rand(vec2(TIME,TIME)), 1.0);
		local_glitch_size = random_offset * glitch_size;
	}
	
	if (local_glitch_size > 0.0)	{
		random.x = rand(vec2(floor(random_offset + xy.y / local_glitch_size) * local_glitch_size, TIME));
		random.y = rand(vec2(floor(random_offset + xy.x / local_glitch_size) * local_glitch_size, TIME));
	}
	else	{
		random.x = rand(vec2(xy.x, TIME));
		random.y = rand(vec2(xy.y, TIME));
	}
	
	if (randomize_zoom)	{
		if ((random.x < glitch_horizontal)&&(random.y < glitch_vertical))	{
			float level = rand(vec2(random.x, random.y)) / 5.0 + 0.90;
			xy = (xy - vec2(0.5))*(1.0/level) + vec2(0.5);
		}
		else if (random.x < glitch_horizontal)	{
			float level = (random.x) + 0.98;
			xy = (xy - vec2(0.5))*(1.0/level) + vec2(0.5);
		}
		else if (random.y < glitch_vertical)	{
			float level = (random.y) + 0.98;
			xy = (xy - vec2(0.5))*(1.0/level) + vec2(0.5);
		}
	}
	
	//	if doing a horizontal glitch do a random shift
	if ((random.x < glitch_horizontal)&&(random.y < glitch_vertical))	{
		vec2 shift = (offset - 0.5);
		shift = shift * rand(shift + random);
		xy.x = mod(xy.x + random.x, 1.0);
		xy.y = mod(xy.y + random.y, 1.0);
		xy = xy + shift;
		shifted = true;
	}
	else if (random.x < glitch_horizontal)	{
		vec2 shift = (offset - 0.5);
		shift = shift * rand(shift + random);
		xy = mod(xy + vec2(0.0, random.x) + shift, 1.0);
		shifted = true;
	}
	else if (random.y < glitch_vertical)	{
		vec2 shift = (offset - 0.5);
		shift = shift * rand(shift + random);
		xy = mod(xy + vec2(random.y, 0.0) + shift, 1.0);
		shifted = true;
	}
	
	if ((shifted) && (use_alt_image) && (rand(vec2(TIME, xy.x)) < 0.5))
		returnMe = IMG_NORM_PIXEL(altImage, xy);
	else
		returnMe = IMG_NORM_PIXEL(inputImage, xy);
	
	gl_FragColor = returnMe;
}
'''


class IsfGlitchShifter(GlslEffect):
    name = 'isf_glitch_shifter'
    label = 'Glitch Shifter'
    description = 'Glitch Shifter (Glitch).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    params = effect_params({
        "glitch_size": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.5,
            "default": 0.1,
            "x-ui": {
                "label": "Size",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "glitch_size"
            }
        },
        "glitch_horizontal": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.2,
            "x-ui": {
                "label": "Horizontal Amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "glitch_horizontal"
            }
        },
        "glitch_vertical": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Vertical Amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "glitch_vertical"
            }
        },
        "randomize_size": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Randomize Size"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "randomize_size"
            }
        },
        "randomize_zoom": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Randomize Zoom"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "randomize_zoom"
            }
        },
        "use_alt_image": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Use Alt Image"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "use_alt_image"
            }
        },
        "offset_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Offset x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "offset",
                "c": 0
            }
        },
        "offset_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Offset y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "offset",
                "c": 1
            }
        }
    })
