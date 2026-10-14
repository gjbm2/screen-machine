"""Flip V: Flip V (Geometry Adjustment).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Flip%20V.fs
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
		"Geometry Adjustment"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		}
	]
}*/

void main() {
	vec2		normSrcCoord;

	normSrcCoord.x = isf_FragNormCoord[0];
	normSrcCoord.y = isf_FragNormCoord[1];

	normSrcCoord.y = (1.0-normSrcCoord.y);

	gl_FragColor = IMG_NORM_PIXEL(inputImage, normSrcCoord);
}'''


class IsfFlipV(GlslEffect):
    name = 'isf_flip_v'
    label = 'Flip V'
    description = 'Flip V (Geometry Adjustment).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    params = effect_params({})
