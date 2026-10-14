"""Exposure Adjust: Exposure Adjust (Color Adjustment).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Exposure%20Adjust.fs
Author: by carter rosenberg
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"CREDIT": "by carter rosenberg",
	"ISFVSN": "2",
	"CATEGORIES": [
		"Color Adjustment"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "inputEV",
			"TYPE": "float",
			"MIN": -10.0,
			"MAX": 10.0,
			"DEFAULT": 0.5
		}
	]
}*/



void main() {
	//	based on
	//	https://developer.apple.com/library/mac/documentation/graphicsimaging/reference/CoreImageFilterReference/Reference/reference.html#//apple_ref/doc/filter/ci/CIExposureAdjust
	vec4		tmpColorA = IMG_THIS_PIXEL(inputImage);
	tmpColorA.rgb = tmpColorA.rgb * pow(2.0, inputEV);
	gl_FragColor = tmpColorA;
}
'''


class IsfExposureAdjust(GlslEffect):
    name = 'isf_exposure_adjust'
    label = 'Exposure Adjust'
    description = 'Exposure Adjust (Color Adjustment).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'colour'
    params = effect_params({
        "input_ev": {
            "type": "number",
            "minimum": -10.0,
            "maximum": 10.0,
            "default": 0.5,
            "x-ui": {
                "label": "Input EV",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "inputEV"
            }
        }
    })
