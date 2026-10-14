"""Mirror: Mirror (Geometry Adjustment).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Mirror.fs
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
		},
		{
			"NAME": "horizontal",
			"TYPE": "bool",
			"DEFAULT": 1.0
		},
		{
			"NAME": "vertical",
			"TYPE": "bool",
			"DEFAULT": 0.0
		}
	]
}*/

void main() {
	//	isf_FragNormCoord[0] and isf_FragNormCoord[1] are my normalized x/y coordinates
	//	if we're not doing a flip in either direction we can just pass thru
	vec2		normSrcCoord;

	normSrcCoord.x = isf_FragNormCoord[0];
	normSrcCoord.y = isf_FragNormCoord[1];

	if ((normSrcCoord.x > 0.5)&&(horizontal))	{
		normSrcCoord.x = (1.0-normSrcCoord.x);
	}
	if ((normSrcCoord.y > 0.5)&&(vertical))	{
		normSrcCoord.y = (1.0-normSrcCoord.y);
	}
	
	gl_FragColor = IMG_NORM_PIXEL(inputImage, normSrcCoord);
}'''


class IsfMirror(GlslEffect):
    name = 'isf_mirror'
    label = 'Mirror'
    description = 'Mirror (Geometry Adjustment).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    params = effect_params({
        "horizontal": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Horizontal"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "horizontal"
            }
        },
        "vertical": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Vertical"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "vertical"
            }
        }
    })
