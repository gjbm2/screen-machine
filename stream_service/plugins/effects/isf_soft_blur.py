"""Soft Blur: Soft Blur (Blur).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Soft%20Blur.fs
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
		"Blur"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "softness",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.9
		},
		{
			"NAME": "depth",
			"TYPE": "float",
			"MIN": 1.0,
			"MAX": 10.0,
			"DEFAULT": 10.0
		}
	],
	"PASSES": [
		{
			"TARGET": "smaller",
			"WIDTH": "max(floor($WIDTH*0.02),1.0)",
			"HEIGHT": "max(floor($HEIGHT*0.02),1.0)"
		},
		{
			"TARGET": "small",
			"WIDTH": "max(floor($WIDTH*0.25),1.0)",
			"HEIGHT": "max(floor($HEIGHT*0.25),1.0)"
		},
		{
		
		}
	]
}*/


//	A simple three pass blur – first reduce the size, then do a weighted blur, then do the same thing 

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



void main()
{
	
	vec4 color = IMG_THIS_NORM_PIXEL(inputImage);
	vec4 colorL = IMG_NORM_PIXEL(inputImage, left_coord);
	vec4 colorR = IMG_NORM_PIXEL(inputImage, right_coord);
	vec4 colorA = IMG_NORM_PIXEL(inputImage, above_coord);
	vec4 colorB = IMG_NORM_PIXEL(inputImage, below_coord);

	vec4 colorLA = IMG_NORM_PIXEL(inputImage, lefta_coord);
	vec4 colorRA = IMG_NORM_PIXEL(inputImage, righta_coord);
	vec4 colorLB = IMG_NORM_PIXEL(inputImage, leftb_coord);
	vec4 colorRB = IMG_NORM_PIXEL(inputImage, rightb_coord);

	vec4 avg = (color + colorL + colorR + colorA + colorB + colorLA + colorRA + colorLB + colorRB) / 9.0;
	
	if (PASSINDEX == 1)	{
		vec4 blur = IMG_THIS_NORM_PIXEL(smaller);
		avg = mix(color, (avg + depth*blur)/(1.0+depth), softness);
	}
	else if (PASSINDEX == 2)	{
		vec4 blur = IMG_THIS_NORM_PIXEL(small);
		avg = mix(color, (avg + depth*blur)/(1.0+depth), softness);
	}
	gl_FragColor = avg;
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


class IsfSoftBlur(GlslEffect):
    name = 'isf_soft_blur'
    label = 'Soft Blur'
    description = 'Soft Blur (Blur).'
    dialect = 'isf'
    GLSL = GLSL
    VS = VS
    stage = 'stylise'
    scale = 0.35
    params = effect_params({
        "softness": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.9,
            "x-ui": {
                "label": "Softness",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "softness"
            }
        },
        "depth": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 10.0,
            "default": 10.0,
            "x-ui": {
                "label": "Depth",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "depth"
            }
        }
    })
