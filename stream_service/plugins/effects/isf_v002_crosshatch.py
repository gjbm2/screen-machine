"""V002 Crosshatch: V002 Crosshatch (Halftone Effect, v002).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/v002%20Crosshatch.fs
Author: by v002
License: CC-BY-NC-SA-3.0
license_class: noncommercial
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
v002 port: v002 sources are CC-BY-NC-SA-3.0.
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"CREDIT": "by v002",
	"ISFVSN": "2",
	"CATEGORIES": [
		"Halftone Effect", "v002"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "invert",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.0
		},
		{
			"NAME": "separation",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 0.5,
			"DEFAULT": 0.25
		},
		{
			"NAME": "greyscale",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.75
		},
		{
			"NAME": "thickness",
			"TYPE": "float",
			"MIN": 0.5,
			"MAX": 1.0,
			"DEFAULT": 0.75
		},
		{
			"NAME": "front",
			"TYPE": "color",
			"DEFAULT": [
				0.5,
				0.0,
				0.25,
				1.0
			]
		},
		{
			"NAME": "back",
			"TYPE": "color",
			"DEFAULT": [
				0.2,
				0.75,
				0.5,
				1.0
			]
		}
	]
}*/


//Original Source
//http://learningwebgl.com/blog/?p=2858

//	Based on https://github.com/v002/v002-Half-Tones/




const vec4 lumacoeff = vec4(0.2126, 0.7152, 0.0722, 0.0);


void main()
{
    vec4 color = IMG_THIS_PIXEL(inputImage);
    float lum = dot(color, lumacoeff);
    vec2 coord = gl_FragCoord.xy;
    
    vec4 colora = mix(front, back, invert);
    vec4 colorb = mix(back, front, invert);
    
    colora.a *= color.a;
    colorb.a *= color.a;
    
    vec4 cout = mix(color,colora, greyscale);

    gl_FragColor = colorb;
    
    if (lum > 1.00)
    {
        if (mod(coord.x + coord.y, thickness) >= separation)
            gl_FragColor = cout;
    }
    
    if (lum > 0.75)
    {
        if (mod(coord.x - coord.y, thickness) >= separation )
            gl_FragColor = cout;
    }
    
    if (lum > 0.50)
    {
        if (mod(coord.x + coord.y - (thickness * 0.5), thickness) >= separation)
            gl_FragColor = cout;
    }
    
    if (lum > 0.3)
    {
        if (mod(coord.x - coord.y - (thickness * 0.5), thickness) >= separation)
            gl_FragColor = cout;
    }
}'''


class IsfV002Crosshatch(GlslEffect):
    name = 'isf_v002_crosshatch'
    label = 'V002 Crosshatch'
    description = 'V002 Crosshatch (Halftone Effect, v002).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'colour'
    params = effect_params({
        "invert": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Invert",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "invert"
            }
        },
        "separation": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.5,
            "default": 0.25,
            "x-ui": {
                "label": "Separation",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "separation"
            }
        },
        "greyscale": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.75,
            "x-ui": {
                "label": "Greyscale",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "greyscale"
            }
        },
        "thickness": {
            "type": "number",
            "minimum": 0.5,
            "maximum": 1.0,
            "default": 0.75,
            "x-ui": {
                "label": "Thickness",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "thickness"
            }
        },
        "front": {
            "type": "string",
            "format": "color",
            "default": "#800040",
            "x-ui": {
                "label": "Front",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "front",
                "color": 4,
                "alpha": 1.0
            }
        },
        "back": {
            "type": "string",
            "format": "color",
            "default": "#33bf80",
            "x-ui": {
                "label": "Back",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "back",
                "color": 4,
                "alpha": 1.0
            }
        }
    })
