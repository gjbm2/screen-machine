"""Posterize: Posterizes an image

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Posterize.fs
Author: VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Retro",
        "Stylize",
        "Color Effect"
    ],
    "CREDIT": "VIDVOX",
    "DESCRIPTION": "Posterizes an image",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 1.25,
            "LABEL": "Gamma",
            "MAX": 2,
            "MIN": 0.5,
            "NAME": "gamma",
            "TYPE": "float"
        },
        {
            "DEFAULT": 6,
            "LABEL": "Quality",
            "MAX": 32,
            "MIN": 3,
            "NAME": "numColors",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2"
}
*/

void main()	{
	
    vec4	c = IMG_THIS_PIXEL(inputImage);

 	c.rgb = pow(c.rgb, vec3(gamma, gamma, gamma));
 	c.rgb = c.rgb * numColors;
 	c.rgb = floor(c.rgb);
 	c.rgb = c.rgb / numColors;
 	c.rgb = pow(c.rgb, vec3(1.0/gamma));

	gl_FragColor = c;
}
'''


class IsfPosterize(GlslEffect):
    name = 'isf_posterize'
    label = 'Posterize'
    description = 'Posterizes an image'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'colour'
    params = effect_params({
        "gamma": {
            "type": "number",
            "minimum": 0.5,
            "maximum": 2.0,
            "default": 1.25,
            "x-ui": {
                "label": "Gamma",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "gamma"
            }
        },
        "num_colors": {
            "type": "number",
            "minimum": 3.0,
            "maximum": 32.0,
            "default": 6.0,
            "x-ui": {
                "label": "Quality",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "numColors"
            }
        }
    })
