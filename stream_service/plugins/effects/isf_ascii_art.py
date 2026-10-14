"""ASCII Art: ASCII Art

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/ASCII%20Art.fs
Author: by VIDVOX (Ported from https://www.shadertoy.com/view/lssGDj)
License: CC-BY-NC-SA-3.0
license_class: noncommercial
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Shadertoy-derived: Shadertoy's default licence applies.
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"DESCRIPTION": "ASCII Art",
	"CREDIT": "by VIDVOX (Ported from https://www.shadertoy.com/view/lssGDj)",
	"ISFVSN": "2",
	"CATEGORIES": [
		"Stylize", "Retro"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "size",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.1
		},
		{
			"NAME": "gamma",
			"TYPE": "float",
			"DEFAULT": 1.0,
			"MIN": 0.5,
			"MAX": 2.0
		},
		{
			"NAME": "tint",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 1.0
		},
		{
			"NAME": "tintColor",
			"TYPE": "color",
			"DEFAULT": [
				0.0,
				1.0,
				0.0,
				1.0
			]
		},
		{
			"NAME": "alphaMode",
			"TYPE": "bool",
			"DEFAULT": 0.0
		}
	]
	
}*/



float character(float n, vec2 p) // some compilers have the word "char" reserved
{
	p = floor(p*vec2(4.0, -4.0) + 2.5);
	if (clamp(p.x, 0.0, 4.0) == p.x && clamp(p.y, 0.0, 4.0) == p.y)
	{
		if (int(mod(n/exp2(p.x + 5.0*p.y), 2.0)) == 1) return 1.0;
	}	
	return 0.0;
}



void main()	{
	float _size = size*36.0+8.0;
	vec2 uv = gl_FragCoord.xy;
	vec4 inputColor = IMG_NORM_PIXEL(inputImage, (floor(uv/_size)*_size/RENDERSIZE.xy));
	vec3 col = inputColor.rgb;
	float gray = (col.r + col.g + col.b)/3.0;
	gray = pow(gray, gamma);
	col = mix(tintColor.rgb, col.rgb, 1.0-tint);
	
	float n =  65536.0;             // .
	if (gray > 0.2) n = 65600.0;    // :
	if (gray > 0.3) n = 332772.0;   // *
	if (gray > 0.4) n = 15255086.0; // o 
	if (gray > 0.5) n = 23385164.0; // &
	if (gray > 0.6) n = 15252014.0; // 8
	if (gray > 0.7) n = 13199452.0; // @
	if (gray > 0.8) n = 11512810.0; // #
		
	vec2 p = mod(uv/(_size/2.0), 2.0) - vec2(1.0);
	col = col*character(n, p);
	float alpha = mix(tintColor.a * inputColor.a, inputColor.a, 1.0-tint);
	if (alphaMode)	{
		alpha = (col.r + col.g + col.b)/3.0;
		alpha = (alpha > 0.01) ? tintColor.a : alpha;
	}
	
	gl_FragColor = vec4(col,alpha);

}
'''


class IsfAsciiArt(GlslEffect):
    name = 'isf_ascii_art'
    label = 'ASCII Art'
    description = 'ASCII Art'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'stylise'
    params = effect_params({
        "size": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.1,
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
        "gamma": {
            "type": "number",
            "minimum": 0.5,
            "maximum": 2.0,
            "default": 1.0,
            "x-ui": {
                "label": "Gamma",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "gamma"
            }
        },
        "tint": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "Tint",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "tint"
            }
        },
        "tint_color": {
            "type": "string",
            "format": "color",
            "default": "#00ff00",
            "x-ui": {
                "label": "Tint Color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "tintColor",
                "color": 4,
                "alpha": 1.0
            }
        },
        "alpha_mode": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Alpha Mode"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "alphaMode"
            }
        }
    })
