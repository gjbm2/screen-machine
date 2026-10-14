"""Solarize: Solarizes an image

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Solarize.fs
Author: by VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"DESCRIPTION": "Solarizes an image",
	"CREDIT": "by VIDVOX",
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
			"NAME": "centerBrightness",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.5
		},
		{
			"NAME": "powerCurve",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 4.0,
			"DEFAULT": 1.0
		},
		{
			"NAME": "colorize",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.0
		},
		{
			"NAME": "inverse",
			"TYPE": "bool",
			"DEFAULT": 1.0
		}
	]
}*/




vec3 rgb2hsv(vec3 c)	{
	vec4 K = vec4(0.0, -1.0 / 3.0, 2.0 / 3.0, -1.0);
	//vec4 p = mix(vec4(c.bg, K.wz), vec4(c.gb, K.xy), step(c.b, c.g));
	//vec4 q = mix(vec4(p.xyw, c.r), vec4(c.r, p.yzx), step(p.x, c.r));
	vec4 p = c.g < c.b ? vec4(c.bg, K.wz) : vec4(c.gb, K.xy);
	vec4 q = c.r < p.x ? vec4(p.xyw, c.r) : vec4(c.r, p.yzx);
	
	float d = q.x - min(q.w, q.y);
	float e = 1.0e-10;
	return vec3(abs(q.z + (q.w - q.y) / (6.0 * d + e)), d / (q.x + e), q.x);
}

vec3 hsv2rgb(vec3 c)	{
	vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
	vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
	return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}




void main()
{
	vec4		inColor = IMG_NORM_PIXEL(inputImage, isf_FragNormCoord);
	vec4		hslColor;
	vec4		outColor;
	
	//	convert to HSV
	hslColor.rgb = rgb2hsv(inColor.rgb);
	outColor.rgb = hslColor.rgb;
	outColor.a = inColor.a;
	
	//	drop the saturation
	//outColor.g = 0.0;
	
	//	adjust the brightness curve
	outColor.b = (outColor.b < centerBrightness) ? (1.0 - outColor.b / centerBrightness) : (outColor.b - centerBrightness) / centerBrightness;
	outColor.b = pow(outColor.b, powerCurve);
	outColor.b = (inverse) ? 1.0 - outColor.b : outColor.b;
	
	outColor.g = (inverse) ? outColor.g * (1.0-hslColor.b) * colorize : outColor.g * hslColor.b * colorize;
	
	//	convert back to rgb
	outColor.rgb = hsv2rgb(outColor.rgb);
	
	gl_FragColor = outColor;
}
'''


class IsfSolarize(GlslEffect):
    name = 'isf_solarize'
    label = 'Solarize'
    description = 'Solarizes an image'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'colour'
    params = effect_params({
        "center_brightness": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Center Brightness",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "centerBrightness"
            }
        },
        "power_curve": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 4.0,
            "default": 1.0,
            "x-ui": {
                "label": "Power Curve",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "powerCurve"
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
        "inverse": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Inverse"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "inverse"
            }
        }
    })
