"""Duotone: Duotone (Color Effect).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Duotone.fs
Author: by zoidberg
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"CREDIT": "by zoidberg",
	"ISFVSN": "2",
	"CATEGORIES": [
		"Color Effect"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "threshold",
			"TYPE": "float",
			"DEFAULT": 0.50
		},
		{
			"NAME": "softness",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1,
			"DEFAULT": 0.0
		},
		{
			"NAME": "brightColor",
			"TYPE": "color",
			"DEFAULT": [
				1.0,
				1.0,
				1.0,
				1.0
			]
		},
		{
			"NAME": "darkColor",
			"TYPE": "color",
			"DEFAULT": [
				0.0,
				0.0,
				0.0,
				1.0
			]
		}
	]
}*/

//const vec4		lumcoeff = vec4(0.299, 0.587, 0.114, 0.0);
const vec4 	lumcoeff = vec4(0.2126, 0.7152, 0.0722, 0.0);

void main() {
	vec4		srcPixel = IMG_THIS_PIXEL(inputImage);
	float		luminance = dot(srcPixel,lumcoeff);
	//gl_FragColor = (luminance>=threshold) ? (brightColor) : (darkColor);
	
	//	if i'm doing hard edges, it's either one color or the other
	if (softness<=0.0)	{
		gl_FragColor = (luminance>=threshold) ? vec4(brightColor.rgb, srcPixel.a) : vec4(darkColor.rgb, srcPixel.a);
	}
	//	else i'm doing soft edges...
	else	{
		//	'softness' is normalized proportion of luminance on either side of threshold to be interpolated
		//	e.g.: 'softness' is 0.5 and 'threshold' is 0.5: vals < 0.25 are "dark", vals from 0.25-0.75 are "interpolated", and vals > 0.75 are "light"
		vec4		midColor = (brightColor+darkColor)/vec4(2.0);
		vec4		dstPixel;
		if (luminance>=threshold)	{
			gl_FragColor = mix(midColor, brightColor, smoothstep(threshold, threshold+((1.0-threshold)*softness), luminance));
		}
		else	{
			gl_FragColor = mix(darkColor, midColor, smoothstep(threshold-((1.0-threshold)*softness), threshold, luminance));
		}
		
		/*
		//	'softness' is the absolute width (in luminance) on either side of the threshold to be interpolated
		//	e.g.: if softness is 0.25 and threshold is 0.5, vals < 0.25 are "dark", vals from 0.25-0.75 are "smoothed", and vals > 0.75 are "light"
		vec4		midColor = (brightColor+darkColor)/vec4(2.0);
		vec4		dstPixel;
		if (luminance>=threshold)	{
			gl_FragColor = mix(midColor, brightColor, smoothstep(threshold, threshold+softness, luminance));
		}
		else	{
			gl_FragColor = mix(darkColor, midColor, smoothstep(threshold-softness, threshold, luminance));
		}
		*/
	}
}
'''


class IsfDuotone(GlslEffect):
    name = 'isf_duotone'
    label = 'Duotone'
    description = 'Duotone (Color Effect).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'colour'
    params = effect_params({
        "threshold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Threshold",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "threshold"
            }
        },
        "softness": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
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
        "bright_color": {
            "type": "string",
            "format": "color",
            "default": "#ffffff",
            "x-ui": {
                "label": "Bright Color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "brightColor",
                "color": 4,
                "alpha": 1.0
            }
        },
        "dark_color": {
            "type": "string",
            "format": "color",
            "default": "#000000",
            "x-ui": {
                "label": "Dark Color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "darkColor",
                "color": 4,
                "alpha": 1.0
            }
        }
    })
