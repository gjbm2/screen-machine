"""False Color: False Color (Color Effect).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/False%20Color.fs
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
	"CATEGORIES": [
		"Color Effect"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "brightColor",
			"TYPE": "color",
			"DEFAULT": [
				1.0,
				0.9,
				0.8,
				1.0
			]
		},
		{
			"NAME": "darkColor",
			"TYPE": "color",
			"DEFAULT": [
				0.3,
				0.0,
				0.0,
				1.0
			]
		}
	]
}*/

//const vec4		lumcoeff = vec4(0.299, 0.587, 0.114, 0.0);
const vec4 	lumcoeff = vec4(0.2126, 0.7152, 0.0722, 0.0);

void main() {
	vec4		srcPixel = IMG_THIS_PIXEL(inputImage);
	float		luminance = dot(srcPixel,lumcoeff);
	gl_FragColor = mix(vec4(darkColor.rgb, srcPixel.a), vec4(brightColor.rgb, srcPixel.a), luminance);
}
'''


class IsfFalseColor(GlslEffect):
    name = 'isf_false_color'
    label = 'False Color'
    description = 'False Color (Color Effect).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'colour'
    params = effect_params({
        "bright_color": {
            "type": "string",
            "format": "color",
            "default": "#ffe6cc",
            "x-ui": {
                "label": "Bright Color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "brightColor",
                "color": 4,
                "alpha": 1.0
            }
        },
        "dark_color": {
            "type": "string",
            "format": "color",
            "default": "#4c0000",
            "x-ui": {
                "label": "Dark Color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "darkColor",
                "color": 4,
                "alpha": 1.0
            }
        }
    })
