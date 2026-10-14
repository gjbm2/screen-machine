"""Color Monochrome: Color Monochrome (Color Effect, Retro).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Color%20Monochrome.fs
Author: by zoidberg
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Color Effect",
        "Retro"
    ],
    "CREDIT": "by zoidberg",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 1,
            "MAX": 1,
            "MIN": 0,
            "NAME": "intensity",
            "TYPE": "float"
        },
        {
            "DEFAULT": [
                0.6,
                0.45,
                0.3,
                1
            ],
            "NAME": "color",
            "TYPE": "color"
        }
    ],
    "ISFVSN": "2"
}
*/


//const vec4		lumcoeff = vec4(0.299, 0.587, 0.114, 0.0);
const vec4 	lumcoeff = vec4(0.2126, 0.7152, 0.0722, 0.0);

void main() {
	vec4		srcPixel = IMG_THIS_PIXEL(inputImage);
	float		luminance = dot(srcPixel,lumcoeff);
	//float		luminance = (srcPixel.r + srcPixel.g + srcPixel.b)/3.0;
	vec4		dstPixel = (luminance >= 0.50)
		? mix(color, vec4(1,1,1,srcPixel.a), (luminance-0.5)*2.0)
		: mix(vec4(0,0,0,srcPixel.a), color, luminance*2.0);
	gl_FragColor = mix(srcPixel, dstPixel, intensity);
}'''


class IsfColorMonochrome(GlslEffect):
    name = 'isf_color_monochrome'
    label = 'Color Monochrome'
    description = 'Color Monochrome (Color Effect, Retro).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'colour'
    params = effect_params({
        "p_intensity": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "Intensity",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "intensity"
            }
        },
        "color": {
            "type": "string",
            "format": "color",
            "default": "#99734c",
            "x-ui": {
                "label": "Color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "color",
                "color": 4,
                "alpha": 1.0
            }
        }
    })
