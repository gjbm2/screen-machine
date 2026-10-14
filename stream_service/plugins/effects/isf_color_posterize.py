"""Color Posterize: Color Posterize (Color Effect, Retro).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Color%20Posterize.fs
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
            "DEFAULT": 30,
            "MAX": 30,
            "MIN": 2,
            "NAME": "levels",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2"
}
*/

void main() {
	//	get the src pixel, convert to HSL, posterize the 'L', convert back to RGB
	vec4		srcPixel = IMG_THIS_PIXEL(inputImage);
	vec4		amountPerLevel = vec4(1.0/levels);
	vec4		numOfLevels = floor(srcPixel/amountPerLevel);
	vec4		outColor = numOfLevels * (vec4(1.0) / (vec4(levels) - vec4(1.0)));
	outColor.a = srcPixel.a;
	gl_FragColor = outColor;
}
'''


class IsfColorPosterize(GlslEffect):
    name = 'isf_color_posterize'
    label = 'Color Posterize'
    description = 'Color Posterize (Color Effect, Retro).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'colour'
    params = effect_params({
        "levels": {
            "type": "number",
            "minimum": 2.0,
            "maximum": 30.0,
            "default": 30.0,
            "x-ui": {
                "label": "Levels",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "levels"
            }
        }
    })
