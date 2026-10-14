"""Ghosting: Ghosting (Stylize, Feedback, Color Effect).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Ghosting.fs
Author: by VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Stylize",
        "Feedback",
        "Color Effect"
    ],
    "CREDIT": "by VIDVOX",
    "DESCRIPTION": "",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": -0.5,
            "LABEL": "Bias",
            "MAX": 0,
            "MIN": -1,
            "NAME": "uBias",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.5,
            "LABEL": "Scale",
            "MAX": 2,
            "MIN": 0,
            "NAME": "uScale",
            "TYPE": "float"
        },
        {
            "DEFAULT": 5,
            "LABEL": "Ghosts",
            "MAX": 5,
            "MIN": 0,
            "NAME": "uGhosts",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.0125,
            "LABEL": "Ghost Dispersal",
            "MAX": 0.1,
            "MIN": 0,
            "NAME": "uGhostDispersal",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1,
            "LABEL": "Additive Mode",
            "NAME": "uAdditive",
            "TYPE": "bool"
        },
        {
            "DEFAULT": [
                0.5,
                0.5
            ],
            "LABEL": "Direction",
            "MAX": [
                1,
                1
            ],
            "MIN": [
                0,
                0
            ],
            "NAME": "uDirection",
            "TYPE": "point2D"
        },
        {
            "DEFAULT": [
                0.9,
                0.8,
                0.7,
                1
            ],
            "LABEL": "Lens Color",
            "NAME": "uLensColor",
            "TYPE": "color"
        }
    ],
    "ISFVSN": "2",
    "PASSES": [
        {
            "DESCRIPTION": "Downsample and threshold",
            "HEIGHT": "floor($HEIGHT/1.0)",
            "TARGET": "downsampleAndThresholdImage",
            "WIDTH": "floor($WIDTH/1.0)"
        },
        {
        }
    ]
}
*/




void main()
{

	if (PASSINDEX == 0)	{
		vec2 loc = isf_FragNormCoord;
		gl_FragColor = max(vec4(0.0), IMG_NORM_PIXEL(inputImage,loc) + uBias) * uScale;
	}
	else if (PASSINDEX == 1)	{
		vec2 texcoord = isf_FragNormCoord;
		vec2 texelSize = 1.0 / RENDERSIZE;
		vec2 direction = vec2(1.0) - uDirection;
		vec2 ghostVec = (direction - texcoord) * uGhostDispersal;
		//vec2 direction = vec2(0.5,0.5);
		vec4 result = vec4(0.0);
		for (int i = 0; i < 5; ++i) { 
			if (float(i)>uGhosts)
				break;
			vec2 offset = fract(texcoord + ghostVec * float(i));

			result += IMG_NORM_PIXEL(downsampleAndThresholdImage, offset) * uLensColor;
		}
		//	apply the alpha
		result.rgb = result.rgb * uLensColor.a;
		if (uAdditive)	{
			result = result + IMG_NORM_PIXEL(inputImage, texcoord);
		}
		else	{
			result = result * IMG_NORM_PIXEL(inputImage, texcoord);
		}
		gl_FragColor = result;
	}

}'''


class IsfGhosting(GlslEffect):
    name = 'isf_ghosting'
    label = 'Ghosting'
    description = 'Ghosting (Stylize, Feedback, Color Effect).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'feedback'
    scale = 0.55
    params = effect_params({
        "u_bias": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 0.0,
            "default": -0.5,
            "x-ui": {
                "label": "Bias",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "uBias"
            }
        },
        "u_scale": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.5,
            "x-ui": {
                "label": "Scale",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "uScale"
            }
        },
        "u_ghosts": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 5.0,
            "default": 5.0,
            "x-ui": {
                "label": "Ghosts",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "uGhosts"
            }
        },
        "u_ghost_dispersal": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.1,
            "default": 0.0125,
            "x-ui": {
                "label": "Ghost Dispersal",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "uGhostDispersal"
            }
        },
        "u_additive": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Additive Mode"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "uAdditive"
            }
        },
        "u_direction_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Direction x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "uDirection",
                "c": 0
            }
        },
        "u_direction_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Direction y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "uDirection",
                "c": 1
            }
        },
        "u_lens_color": {
            "type": "string",
            "format": "color",
            "default": "#e6ccb2",
            "x-ui": {
                "label": "Lens Color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "uLensColor",
                "color": 4,
                "alpha": 1.0
            }
        }
    })
