"""Zooming Feedback: Creates a simple zooming feedback loop

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Zooming%20Feedback.fs
Author: VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Retro",
        "Feedback"
    ],
    "CREDIT": "",
    "DESCRIPTION": "Creates a simple zooming feedback loop",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
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
            "NAME": "preShift",
            "TYPE": "point2D"
        },
        {
            "DEFAULT": 0.9,
            "MAX": 1,
            "MIN": 0,
            "NAME": "feedbackLevel",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.5,
            "MAX": 1,
            "MIN": 0,
            "NAME": "rotateAngle",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1.2,
            "MAX": 2,
            "MIN": 0.25,
            "NAME": "zoomLevel",
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
            "NAME": "zoomCenter",
            "TYPE": "point2D"
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
            "NAME": "feedbackShift",
            "TYPE": "point2D"
        },
        {
            "DEFAULT": 0,
            "NAME": "invert",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 3,
            "LABELS": [
                "Add",
                "Over Black",
                "Over Alpha",
                "Max",
                "Under Black",
                "Under Alpha"
            ],
            "NAME": "blendMode",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2,
                3,
                4,
                5
            ]
        },
        {
            "DEFAULT": 0.1,
            "MAX": 1,
            "MIN": 0,
            "NAME": "blackThresh",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1,
            "MAX": 2,
            "MIN": 0,
            "NAME": "satLevel",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "MAX": 1,
            "MIN": 0,
            "NAME": "colorShift",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2",
    "PASSES": [
        {
            "PERSISTENT": true,
            "TARGET": "feedbackBuffer"
        }
    ]
}
*/


const float pi = 3.14159265359;


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



void main()	{
	vec2		loc = isf_FragNormCoord;
	vec4		inputPixelColor = IMG_NORM_PIXEL(inputImage,loc + (0.5 - (preShift)));
	
	vec4		feedbackPixelColor = vec4(0.0);
	
	//	rotate here if needed
	loc = loc * RENDERSIZE;
	float		r = distance(RENDERSIZE/2.0, loc);
	float		a = atan ((loc.y-RENDERSIZE.y/2.0),(loc.x-RENDERSIZE.x/2.0));

	loc.x = r * cos(a + 2.0 * pi * rotateAngle - pi) + 0.5;
	loc.y = r * sin(a + 2.0 * pi * rotateAngle - pi) + 0.5;
	
	loc = loc / RENDERSIZE + vec2(0.5);
	
	vec2		modifiedCenter = zoomCenter;
	loc.x = (loc.x - modifiedCenter.x)*(1.0/zoomLevel) + modifiedCenter.x;
	loc.y = (loc.y - modifiedCenter.y)*(1.0/zoomLevel) + modifiedCenter.y;
	loc += (0.5 - (feedbackShift));
	
	if ((loc.x < 0.0)||(loc.y < 0.0)||(loc.x > 1.0)||(loc.y > 1.0))	{
		feedbackPixelColor = vec4(0.0);
	}
	else	{
		feedbackPixelColor = IMG_NORM_PIXEL(feedbackBuffer,loc);
	}
	
	feedbackPixelColor.rgb = rgb2hsv(feedbackPixelColor.rgb);
	feedbackPixelColor.r = mod(feedbackPixelColor.r+colorShift,1.0);
	feedbackPixelColor.g *= satLevel;
	
	feedbackPixelColor.rgb = hsv2rgb(feedbackPixelColor.rgb);
	
	if (invert)
		feedbackPixelColor.rgb = 1.0 - feedbackPixelColor.rgb;
	
	if (blendMode == 0)	{
		inputPixelColor = inputPixelColor + feedbackLevel * feedbackPixelColor;
	}
	else if (blendMode == 1)	{
		float		val = inputPixelColor.a * (inputPixelColor.r + inputPixelColor.g + inputPixelColor.b) / 3.0;
		inputPixelColor = (val >= blackThresh) ? inputPixelColor : feedbackLevel * feedbackPixelColor;
		inputPixelColor.a = inputPixelColor.a + feedbackPixelColor.a * feedbackLevel;
	}
	else if (blendMode == 2)	{
		//	apply the alpha to the input pixel as this happens
		inputPixelColor.rgb = inputPixelColor.a * inputPixelColor.rgb + (1.0 - inputPixelColor.a) * feedbackLevel * feedbackPixelColor.rgb;
		inputPixelColor.a = inputPixelColor.a + feedbackPixelColor.a * feedbackLevel;
	}
	else if (blendMode == 3)	{
		inputPixelColor.rgb = max(inputPixelColor.a * inputPixelColor.rgb, feedbackLevel * feedbackPixelColor.rgb);
		inputPixelColor.a = inputPixelColor.a + feedbackPixelColor.a * feedbackLevel;
	}
	else if (blendMode == 4)	{
		float		val = feedbackPixelColor.a * (feedbackPixelColor.r + feedbackPixelColor.g + feedbackPixelColor.b) / 3.0;
		inputPixelColor = (val < blackThresh) ? inputPixelColor.a * inputPixelColor : feedbackLevel * feedbackPixelColor;
		inputPixelColor.a = inputPixelColor.a + feedbackPixelColor.a * feedbackLevel;
	}
	else if (blendMode == 5)	{
		//	apply the alpha to the input pixel as this happens
		inputPixelColor.rgb = (1.0-feedbackPixelColor.a) * inputPixelColor.a * inputPixelColor.rgb + feedbackLevel * feedbackPixelColor.rgb;
		inputPixelColor.a = inputPixelColor.a + feedbackPixelColor.a * feedbackLevel;
	}
	
	gl_FragColor = inputPixelColor;
}
'''


class IsfZoomingFeedback(GlslEffect):
    name = 'isf_zooming_feedback'
    label = 'Zooming Feedback'
    description = 'Creates a simple zooming feedback loop'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'feedback'
    scale = 0.35
    params = effect_params({
        "pre_shift_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Pre Shift x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "preShift",
                "c": 0
            }
        },
        "pre_shift_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Pre Shift y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "preShift",
                "c": 1
            }
        },
        "feedback_level": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.9,
            "x-ui": {
                "label": "Feedback Level",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "feedbackLevel"
            }
        },
        "rotate_angle": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Rotate Angle",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "rotateAngle"
            }
        },
        "zoom_level": {
            "type": "number",
            "minimum": 0.25,
            "maximum": 2.0,
            "default": 1.2,
            "x-ui": {
                "label": "Zoom Level",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "zoomLevel"
            }
        },
        "zoom_center_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Zoom Center x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "zoomCenter",
                "c": 0
            }
        },
        "zoom_center_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Zoom Center y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "zoomCenter",
                "c": 1
            }
        },
        "feedback_shift_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Feedback Shift x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "feedbackShift",
                "c": 0
            }
        },
        "feedback_shift_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Feedback Shift y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "feedbackShift",
                "c": 1
            }
        },
        "invert": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Invert"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "invert"
            }
        },
        "blend_mode": {
            "type": "integer",
            "enum": [
                0,
                1,
                2,
                3,
                4,
                5
            ],
            "default": 3,
            "x-ui": {
                "label": "Blend Mode",
                "widget": "select",
                "labels": [
                    "Add",
                    "Over Black",
                    "Over Alpha",
                    "Max",
                    "Under Black",
                    "Under Alpha"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "blendMode"
            }
        },
        "black_thresh": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.1,
            "x-ui": {
                "label": "Black Thresh",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "blackThresh"
            }
        },
        "sat_level": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 1.0,
            "x-ui": {
                "label": "Sat Level",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "satLevel"
            }
        },
        "color_shift": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Color Shift",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "colorShift"
            }
        }
    })
