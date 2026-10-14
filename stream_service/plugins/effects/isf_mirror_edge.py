"""Mirror Edge: Mirror Edge (Tile Effect).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Mirror%20Edge.fs
Author: by VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Tile Effect"
    ],
    "CREDIT": "by VIDVOX",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Angle",
            "MAX": 1,
            "MIN": 0,
            "NAME": "angle",
            "TYPE": "float"
        },
        {
            "DEFAULT": [
                0,
                0.5
            ],
            "LABEL": "Shift",
            "MAX": [
                1,
                1
            ],
            "MIN": [
                0,
                0
            ],
            "NAME": "shift",
            "TYPE": "point2D"
        }
    ],
    "ISFVSN": "2"
}
*/

#if __VERSION__ <= 120
varying vec2 translated_coord;
#else
in vec2 translated_coord;
#endif


void main() {
	vec2 loc = translated_coord;
	vec2 modifiedCenter = shift;
	
	loc = mod(loc + modifiedCenter, 1.0);
	
	//	mirror the image so it's repeated 4 times at different reflections
	loc = 2.0 * abs(loc - 0.5);
	
	gl_FragColor = IMG_NORM_PIXEL(inputImage, loc);
}'''

VS = r'''#if __VERSION__ <= 120
varying vec2 translated_coord;
#else
out vec2 translated_coord;
#endif

const float pi = 3.14159265359;

void main()	{
	isf_vertShaderInit();

	vec2 loc = RENDERSIZE * vec2(isf_FragNormCoord[0],isf_FragNormCoord[1]);
	
	float r = distance(RENDERSIZE/2.0, loc);
	float a = atan ((loc.y-RENDERSIZE.y/2.0),(loc.x-RENDERSIZE.x/2.0));

	loc.x = r * cos(a + 2.0 * pi * angle) + 0.5;
	loc.y = r * sin(a + 2.0 * pi * angle) + 0.5;
	
	translated_coord = loc / RENDERSIZE + vec2(0.5);

}'''


class IsfMirrorEdge(GlslEffect):
    name = 'isf_mirror_edge'
    label = 'Mirror Edge'
    description = 'Mirror Edge (Tile Effect).'
    dialect = 'isf'
    GLSL = GLSL
    VS = VS
    stage = 'warp'
    params = effect_params({
        "angle": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Angle",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "angle"
            }
        },
        "shift_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Shift x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "shift",
                "c": 0
            }
        },
        "shift_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Shift y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "shift",
                "c": 1
            }
        }
    })
