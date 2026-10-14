"""Sharpen RGB: Sharpen RGB (Sharpen).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Sharpen%20RGB.fs
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
		"Sharpen"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "intensityR",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 10.0,
			"DEFAULT": 1.0
		},
		{
			"NAME": "intensityG",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 10.0,
			"DEFAULT": 1.0
		},
		{
			"NAME": "intensityB",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 10.0,
			"DEFAULT": 1.0
		}
	]
}*/

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

float gray(vec4 n)
{
	return (n.r + n.g + n.b)/3.0;
}

void main()
{

	vec4 color = IMG_THIS_PIXEL(inputImage);
	vec4 colorL = IMG_NORM_PIXEL(inputImage, left_coord);
	vec4 colorR = IMG_NORM_PIXEL(inputImage, right_coord);
	vec4 colorA = IMG_NORM_PIXEL(inputImage, above_coord);
	vec4 colorB = IMG_NORM_PIXEL(inputImage, below_coord);

	vec4 colorLA = IMG_NORM_PIXEL(inputImage, lefta_coord);
	vec4 colorRA = IMG_NORM_PIXEL(inputImage, righta_coord);
	vec4 colorLB = IMG_NORM_PIXEL(inputImage, leftb_coord);
	vec4 colorRB = IMG_NORM_PIXEL(inputImage, rightb_coord);

	vec4 final = color;
	final.r = color.r + intensityR * (8.0*color.r - colorL.r - colorR.r - colorA.r - colorB.r - colorLA.r - colorRA.r - colorLB.r - colorRB.r);
	final.g = color.g + intensityG * (8.0*color.g - colorL.g - colorR.g - colorA.g - colorB.g - colorLA.g - colorRA.g - colorLB.g - colorRB.g);
	final.b = color.b + intensityB * (8.0*color.b - colorL.b - colorR.b - colorA.b - colorB.b - colorLA.b - colorRA.b - colorLB.b - colorRB.b);
	
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


class IsfSharpenRgb(GlslEffect):
    name = 'isf_sharpen_rgb'
    label = 'Sharpen RGB'
    description = 'Sharpen RGB (Sharpen).'
    dialect = 'isf'
    GLSL = GLSL
    VS = VS
    stage = 'colour'
    scale = 0.35
    params = effect_params({
        "intensity_r": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.0,
            "x-ui": {
                "label": "Intensity R",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "intensityR"
            }
        },
        "intensity_g": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.0,
            "x-ui": {
                "label": "Intensity G",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "intensityG"
            }
        },
        "intensity_b": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.0,
            "x-ui": {
                "label": "Intensity B",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "intensityB"
            }
        }
    })
