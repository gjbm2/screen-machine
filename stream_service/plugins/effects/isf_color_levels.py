"""Color Levels: Color Levels (Color Adjustment).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Color%20Levels.fs
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
		"Color Adjustment"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "minLevel",
			"LABEL": "Minimum Point",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.2
		},
		{
			"NAME": "midLevel",
			"LABEL": "Mid Point",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.5
		},
		{
			"NAME": "maxLevel",
			"LABEL": "Maximum Point",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.8
		},
		{
			"NAME": "offset1",
			"TYPE": "color",
			"DEFAULT": [
				0.5,
				0.5,
				0.5,
				0.5
			]
		},
		{
			"NAME": "offset2",
			"TYPE": "color",
			"DEFAULT": [
				0.5,
				0.5,
				0.5,
				0.5
			]
		},
		{
			"NAME": "offset3",
			"TYPE": "color",
			"DEFAULT": [
				0.5,
				0.5,
				0.5,
				0.5
			]
		},
		{
			"NAME": "offset4",
			"TYPE": "color",
			"DEFAULT": [
				0.5,
				0.5,
				0.5,
				0.5
			]
		},
		{
			"NAME": "offset5",
			"TYPE": "color",
			"DEFAULT": [
				0.5,
				0.5,
				0.5,
				0.5
			]
		},
		{
			"NAME": "offset6",
			"TYPE": "color",
			"DEFAULT": [
				0.5,
				0.5,
				0.5,
				0.5
			]
		},
		{
			"NAME": "levelsMode",
			"LABEL": "Levels Mode",
			"TYPE": "bool",
			"DEFAULT": 0.0
		}
	]
}*/



void main() {
	vec4		tmpColor = IMG_THIS_PIXEL(inputImage);
	float brightness = (tmpColor.r + tmpColor.g + tmpColor.b) * tmpColor.a / 3.0;
	
	//	all adjustments
	if (brightness <= minLevel)	{
		tmpColor = tmpColor + offset1 - 0.5;
	}
	else if (brightness <= (minLevel + midLevel)/2.0)	{
		tmpColor = tmpColor + offset2 - 0.5;
	}
	else if (brightness <= midLevel)	{
		tmpColor = tmpColor + offset3 - 0.5;
	}
	else if (brightness <= (maxLevel + midLevel)/2.0)	{
		tmpColor = tmpColor + offset4 - 0.5;
	}
	else if (brightness <= maxLevel)	{
		tmpColor = tmpColor + offset5 - 0.5;
	}
	else	{
		tmpColor = tmpColor + offset6 - 0.5;
	}	
	
	if (levelsMode)	{
		//	all adjustments
		tmpColor.rgb = vec3(1.0);
		
		if (brightness <= minLevel)	{
			tmpColor.a = 0.0;
		}
		else if (brightness <= (minLevel + midLevel)/2.0)	{
			tmpColor.a = 1.0/5.0;
		}
		else if (brightness <= midLevel)	{
			tmpColor.a = 2.0/5.0;
		}
		else if (brightness <= (maxLevel + midLevel)/2.0)	{
			tmpColor.a = 3.0/5.0;
		}
		else if (brightness <= maxLevel)	{
			tmpColor.a = 4.0/5.0;
		}
		else	{
			tmpColor.a = 1.0;
		}
	}
	gl_FragColor = clamp(tmpColor, 0.0, 1.0);
}

'''


class IsfColorLevels(GlslEffect):
    name = 'isf_color_levels'
    label = 'Color Levels'
    description = 'Color Levels (Color Adjustment).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'colour'
    params = effect_params({
        "min_level": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.2,
            "x-ui": {
                "label": "Minimum Point",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "minLevel"
            }
        },
        "mid_level": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Mid Point",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "midLevel"
            }
        },
        "max_level": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.8,
            "x-ui": {
                "label": "Maximum Point",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "maxLevel"
            }
        },
        "offset1": {
            "type": "string",
            "format": "color",
            "default": "#808080",
            "x-ui": {
                "label": "Offset1",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "offset1",
                "color": 4,
                "alpha": 0.5
            }
        },
        "offset2": {
            "type": "string",
            "format": "color",
            "default": "#808080",
            "x-ui": {
                "label": "Offset2",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "offset2",
                "color": 4,
                "alpha": 0.5
            }
        },
        "offset3": {
            "type": "string",
            "format": "color",
            "default": "#808080",
            "x-ui": {
                "label": "Offset3",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "offset3",
                "color": 4,
                "alpha": 0.5
            }
        },
        "offset4": {
            "type": "string",
            "format": "color",
            "default": "#808080",
            "x-ui": {
                "label": "Offset4",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "offset4",
                "color": 4,
                "alpha": 0.5
            }
        },
        "offset5": {
            "type": "string",
            "format": "color",
            "default": "#808080",
            "x-ui": {
                "label": "Offset5",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "offset5",
                "color": 4,
                "alpha": 0.5
            }
        },
        "offset6": {
            "type": "string",
            "format": "color",
            "default": "#808080",
            "x-ui": {
                "label": "Offset6",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "offset6",
                "color": 4,
                "alpha": 0.5
            }
        },
        "levels_mode": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Levels Mode"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "levelsMode"
            }
        }
    })
