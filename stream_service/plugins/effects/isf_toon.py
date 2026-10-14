"""Toon: Toon (Stylize).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Toon.fs
Author: by VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''
/*{
	"CREDIT": "by VIDVOX",
	"ISFVSN": "2",
	"CATEGORIES": [
		"Stylize"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "normalEdgeThreshold",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 0.1,
			"DEFAULT": 0.03
		},
		{
			"NAME": "qLevel",
			"TYPE": "float",
			"MIN": 2.0,
			"MAX": 64.0,
			"DEFAULT": 32.0
		}
	]
}*/

//	with help from https://github.com/neilmendoza/ofxPostProcessing/blob/master/src/ToonPass.cpp


vec3 getNormal(vec2 st){
	vec2 texcoord = clamp(st, 0.001, 0.999);
	return IMG_NORM_PIXEL(inputImage,texcoord).rgb;
}

void main(void){
	float dxtex = 1.0 / RENDERSIZE.x;
	float dytex = 1.0 / RENDERSIZE.y;

	vec2 st = vec2(isf_FragNormCoord[0],isf_FragNormCoord[1]);
	// access center pixel and 4 surrounded pixel
	vec3 center = getNormal(st).rgb;
	vec3 left = getNormal(st + vec2(dxtex, 0.0)).rgb;
	vec3 right = getNormal(st + vec2(-dxtex, 0.0)).rgb;
	vec3 up = getNormal(st + vec2(0.0, -dytex)).rgb;
	vec3 down = getNormal(st + vec2(0.0, dytex)).rgb;

	// discrete Laplace operator
	vec3 laplace = abs(-4.0*center + left + right + up + down);
	// if one rgb-component of convolution result is over threshold => edge
	vec4 line = IMG_NORM_PIXEL(inputImage, st);
	if(laplace.r > normalEdgeThreshold
	|| laplace.g > normalEdgeThreshold
	|| laplace.b > normalEdgeThreshold){
		line = vec4(0.0, 0.0, 0.0, 1.0); // => color the pixel green
	} else {
		line = vec4(1.0, 1.0, 1.0, 1.0); // black
	}

	//end Line;
	//gl_FragColor = line;
	
	vec4 color = IMG_THIS_PIXEL(inputImage);

	// store previous alpha value
	float alpha = color.a;
	// quantize process: multiply by factor, round and divde by factor
	color = floor((qLevel * color)) / qLevel;
	// set fragment/pixel color
	color.a = alpha;

	gl_FragColor = color * line;
	
}

'''


class IsfToon(GlslEffect):
    name = 'isf_toon'
    label = 'Toon'
    description = 'Toon (Stylize).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'stylise'
    scale = 0.7
    params = effect_params({
        "normal_edge_threshold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.1,
            "default": 0.03,
            "x-ui": {
                "label": "Normal Edge Threshold",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "normalEdgeThreshold"
            }
        },
        "q_level": {
            "type": "number",
            "minimum": 2.0,
            "maximum": 64.0,
            "default": 32.0,
            "x-ui": {
                "label": "Q Level",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "qLevel"
            }
        }
    })
