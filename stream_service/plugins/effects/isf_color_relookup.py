"""Color Relookup: Color Relookup (Glitch).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Color%20Relookup.fs
Author: by VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"CREDIT": "by VIDVOX",
	"ISFVSN": "2",
	"CATEGORIES": [
		"Glitch"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "lookupImage",
			"TYPE": "image"
		},
		{
			"NAME": "mix_amount",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.5
		}
	]
}*/

void main() {
	vec4 thisColor = IMG_THIS_PIXEL(inputImage);
	vec2 lookupCoord;
	lookupCoord.x = mix (thisColor.r, thisColor.g, mix_amount);
	lookupCoord.x = mix (lookupCoord.x, thisColor.b, mix_amount);
	lookupCoord.x = RENDERSIZE.x * lookupCoord.x / 255.0;
	
	lookupCoord.y = mix (thisColor.r, thisColor.g, mix_amount);
	lookupCoord.y = mix (lookupCoord.y, thisColor.b, mix_amount);
	lookupCoord.y = RENDERSIZE.y * lookupCoord.y / 255.0;
	
	gl_FragColor = IMG_NORM_PIXEL(lookupImage, lookupCoord);
}'''


class IsfColorRelookup(GlslEffect):
    name = 'isf_color_relookup'
    label = 'Color Relookup'
    description = 'Color Relookup (Glitch).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    params = effect_params({
        "mix_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Mix amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mix_amount"
            }
        }
    })
