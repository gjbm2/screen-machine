"""Dot Screen: Dot Screen (Halftone Effect, Retro).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Dot%20Screen.fs
Author: by VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Halftone Effect",
        "Retro"
    ],
    "CREDIT": "by VIDVOX",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 1,
            "MAX": 10,
            "MIN": 0,
            "NAME": "sharpness",
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
            "DEFAULT": 1,
            "MAX": 2,
            "MIN": 0,
            "NAME": "scale",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "MAX": 1,
            "MIN": 0,
            "NAME": "colorize",
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
            "NAME": "center",
            "TYPE": "point2D"
        }
    ],
    "ISFVSN": "2"
}
*/

#if __VERSION__ <= 120
varying vec2 left_coord;
varying vec2 right_coord;
varying vec2 above_coord;
varying vec2 below_coord;

varying vec2 lefta_coord;
varying vec2 righta_coord;
varying vec2 leftb_coord;
varying vec2 rightb_coord;
#else
in vec2 left_coord;
in vec2 right_coord;
in vec2 above_coord;
in vec2 below_coord;

in vec2 lefta_coord;
in vec2 righta_coord;
in vec2 leftb_coord;
in vec2 rightb_coord;
#endif

const float tau = 6.28318530718;

float pattern() {
	float s = sin( angle * tau ), c = cos( angle * tau );
	vec2 tex = (isf_FragNormCoord - center) * RENDERSIZE;
	vec2 point = vec2( c * tex.x - s * tex.y, s * tex.x + c * tex.y ) * max(scale,0.001);
	return ( sin( point.x ) * sin( point.y ) ) * 4.0;
}

void main() {
	vec4 color = IMG_THIS_PIXEL(inputImage);
	vec4 colorL = IMG_NORM_PIXEL(inputImage, left_coord);
	vec4 colorR = IMG_NORM_PIXEL(inputImage, right_coord);
	vec4 colorA = IMG_NORM_PIXEL(inputImage, above_coord);
	vec4 colorB = IMG_NORM_PIXEL(inputImage, below_coord);

	vec4 colorLA = IMG_NORM_PIXEL(inputImage, lefta_coord);
	vec4 colorRA = IMG_NORM_PIXEL(inputImage, righta_coord);
	vec4 colorLB = IMG_NORM_PIXEL(inputImage, leftb_coord);
	vec4 colorRB = IMG_NORM_PIXEL(inputImage, rightb_coord);

	vec4 final = color + sharpness * (8.0*color - colorL - colorR - colorA - colorB - colorLA - colorRA - colorLB - colorRB);
	
	float average = ( final.r + final.g + final.b ) / 3.0;
	final = vec4( vec3( average * 10.0 - 5.0 + pattern() ), color.a );
	final = mix (color * final, final, 1.0-colorize);
	gl_FragColor = final;
}'''

VS = r'''#if __VERSION__ <= 120
varying vec2 left_coord;
varying vec2 right_coord;
varying vec2 above_coord;
varying vec2 below_coord;

varying vec2 lefta_coord;
varying vec2 righta_coord;
varying vec2 leftb_coord;
varying vec2 rightb_coord;
#else
out vec2 left_coord;
out vec2 right_coord;
out vec2 above_coord;
out vec2 below_coord;

out vec2 lefta_coord;
out vec2 righta_coord;
out vec2 leftb_coord;
out vec2 rightb_coord;
#endif


void main()
{
	isf_vertShaderInit();
	vec2 texc = vec2(isf_FragNormCoord[0],isf_FragNormCoord[1]);
	vec2 d = 1.0/RENDERSIZE;
	
	left_coord = clamp(vec2(texc.xy + vec2(-d.x , 0)),0.0,1.0);
	right_coord = clamp(vec2(texc.xy + vec2(d.x , 0)),0.0,1.0);
	above_coord = clamp(vec2(texc.xy + vec2(0,d.y)),0.0,1.0);
	below_coord = clamp(vec2(texc.xy + vec2(0,-d.y)),0.0,1.0);

	lefta_coord = clamp(vec2(texc.xy + vec2(-d.x , d.x)),0.0,1.0);
	righta_coord = clamp(vec2(texc.xy + vec2(d.x , d.x)),0.0,1.0);
	leftb_coord = clamp(vec2(texc.xy + vec2(-d.x , -d.x)),0.0,1.0);
	rightb_coord = clamp(vec2(texc.xy + vec2(d.x , -d.x)),0.0,1.0);
}'''


class IsfDotScreen(GlslEffect):
    name = 'isf_dot_screen'
    label = 'Dot Screen'
    description = 'Dot Screen (Halftone Effect, Retro).'
    dialect = 'isf'
    GLSL = GLSL
    VS = VS
    stage = 'colour'
    scale = 0.35
    params = effect_params({
        "sharpness": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.0,
            "x-ui": {
                "label": "Sharpness",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "sharpness"
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
        "scale": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 1.0,
            "x-ui": {
                "label": "Scale",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "scale"
            }
        },
        "colorize": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Colorize",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "colorize"
            }
        },
        "center_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Center x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "center",
                "c": 0
            }
        },
        "center_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Center y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "center",
                "c": 1
            }
        }
    })
