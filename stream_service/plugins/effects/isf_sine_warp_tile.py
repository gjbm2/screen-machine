"""Sine Warp Tile: Sine Warp Tile (Kaleidoscope, Tile Effect).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Sine%20Warp%20Tile.fs
Author: by VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Kaleidoscope",
        "Tile Effect"
    ],
    "CREDIT": "by VIDVOX",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0.5,
            "MAX": 0.5,
            "MIN": 0,
            "NAME": "size",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "MAX": 1,
            "MIN": 0,
            "NAME": "rotation",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "MAX": 1,
            "MIN": 0,
            "NAME": "angle",
            "TYPE": "float"
        },
        {
            "DEFAULT": [
                0.5,
                0.5
            ],
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


const float tau = 6.28318530718;


vec2 pattern() {
	float s = sin(tau * rotation * 0.5);
	float c = cos(tau * rotation * 0.5);
	vec2 tex = isf_FragNormCoord;
	float scale = 1.0 / max(size,0.001);
	vec2 point = vec2( c * tex.x - s * tex.y, s * tex.x + c * tex.y ) * scale;
	point = point - scale * shift;
	//	do the sine distort
	point = 0.5 + 0.5 * vec2( sin(scale * point.x), sin(scale * point.y));
	
	//	now do a rotation
	vec2 center = vec2(0.5,0.5);
	float r = distance(center, point);
	float a = atan ((point.y-center.y),(point.x-center.x));
	
	s = sin(a + tau * angle);
	c = cos(a + tau * angle);
	
	float zoom = max(abs(s),abs(c))*RENDERSIZE.x / RENDERSIZE.y;
	
	point.x = (r * c)/zoom + 0.5;
	point.y = (r * s)/zoom + 0.5;

	return point;
}


void main() {

	vec2 pat = pattern();

	gl_FragColor = IMG_NORM_PIXEL(inputImage,pat);
}'''


class IsfSineWarpTile(GlslEffect):
    name = 'isf_sine_warp_tile'
    label = 'Sine Warp Tile'
    description = 'Sine Warp Tile (Kaleidoscope, Tile Effect).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    scale = 0.9
    params = effect_params({
        "size": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.5,
            "default": 0.5,
            "x-ui": {
                "label": "Size",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "size"
            }
        },
        "rotation": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Rotation",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "rotation"
            }
        },
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
            "default": 0.5,
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
