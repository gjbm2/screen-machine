"""CV Motion Heat Datamosh: Motion heat selects delayed frames, block offsets, and quantization

Source: https://github.com/daitomanabe/isf-shaders-for-vj/blob/HEAD/shaders/CV%20Motion%20Heat%20Datamosh.fs
Author: by daito / Codex
License: MIT
license_class: permissive
Imported verbatim from GLSL (daitomanabe/isf-shaders-for-vj); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository daitomanabe/isf-shaders-for-vj LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"DESCRIPTION": "Motion heat selects delayed frames, block offsets, and quantization",
	"CREDIT": "by daito / Codex",
	"ISFVSN": "2",
	"CATEGORIES": [
		"Glitch",
		"Color Effect"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "heatGain",
			"LABEL": "Heat Gain",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 10.0,
			"DEFAULT": 4.0
		},
		{
			"NAME": "heatHold",
			"LABEL": "Heat Hold",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.88
		},
		{
			"NAME": "blockSize",
			"LABEL": "Block Size",
			"TYPE": "float",
			"MIN": 1.0,
			"MAX": 96.0,
			"DEFAULT": 18.0
		},
		{
			"NAME": "offsetAmount",
			"LABEL": "Offset Amount",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.35
		},
		{
			"NAME": "posterize",
			"LABEL": "Posterize",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.35
		},
		{
			"NAME": "colorHeat",
			"LABEL": "Color Heat",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.25
		},
		{
			"NAME": "resetNow",
			"LABEL": "Reset",
			"TYPE": "event"
		},
		{
			"NAME": "showHeat",
			"LABEL": "Show Heat",
			"TYPE": "bool",
			"DEFAULT": false
		}
	],
	"PASSES": [
		{
			"TARGET": "heatBuffer",
			"PERSISTENT": true,
			"FLOAT": true
		},
		{
			"TARGET": "lastFrameBuffer",
			"PERSISTENT": true
		},
		{
			"TARGET": "delay4",
			"PERSISTENT": true
		},
		{
			"TARGET": "delay3",
			"PERSISTENT": true
		},
		{
			"TARGET": "delay2",
			"PERSISTENT": true
		},
		{
			"TARGET": "delay1",
			"PERSISTENT": true
		},
		{
		
		}
	]
}*/

const vec3 lumaCoeffs = vec3(0.2126, 0.7152, 0.0722);

float rand(vec2 co)
{
	return fract(sin(dot(co.xy, vec2(12.9898, 78.233))) * 43758.5453);
}

float luma(vec4 color)
{
	return dot(color.rgb, lumaCoeffs);
}

vec4 heatColor(float heat)
{
	vec3 cold = vec3(0.05, 0.15, 0.6);
	vec3 warm = vec3(1.0, 0.25, 0.05);
	vec3 mid = vec3(0.0, 0.9, 0.8);
	return vec4(mix(mix(cold, mid, smoothstep(0.0, 0.5, heat)), warm, smoothstep(0.45, 1.0, heat)), 1.0);
}

void main()
{
	vec2 uv = isf_FragNormCoord.xy;
	bool doReset = ((FRAMEINDEX <= 1) || resetNow);
	vec4 inputPixel = IMG_THIS_NORM_PIXEL(inputImage);
	
	if (PASSINDEX == 0)	{
		if (doReset)	{
			gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);
		}
		else	{
			float motion = abs(luma(inputPixel) - luma(IMG_NORM_PIXEL(lastFrameBuffer, uv))) * heatGain;
			float oldHeat = IMG_THIS_NORM_PIXEL(heatBuffer).r;
			float heat = max(motion, oldHeat * heatHold);
			gl_FragColor = vec4(heat, heat, heat, 1.0);
		}
	}
	else if (PASSINDEX == 1)	{
		gl_FragColor = inputPixel;
	}
	else if (PASSINDEX == 2)	{
		gl_FragColor = doReset ? inputPixel : IMG_THIS_NORM_PIXEL(delay3);
	}
	else if (PASSINDEX == 3)	{
		gl_FragColor = doReset ? inputPixel : IMG_THIS_NORM_PIXEL(delay2);
	}
	else if (PASSINDEX == 4)	{
		gl_FragColor = doReset ? inputPixel : IMG_THIS_NORM_PIXEL(delay1);
	}
	else if (PASSINDEX == 5)	{
		gl_FragColor = inputPixel;
	}
	else	{
		float heat = clamp(IMG_THIS_NORM_PIXEL(heatBuffer).r, 0.0, 1.0);
		if (showHeat)	{
			gl_FragColor = heatColor(heat);
		}
		else	{
			vec2 cell = max(vec2(blockSize) / RENDERSIZE.xy, vec2(1.0) / RENDERSIZE.xy);
			vec2 id = floor(uv / cell);
			vec2 offset = (vec2(rand(id + vec2(TIME, 2.0)), rand(id + vec2(4.0, TIME))) - vec2(0.5)) * cell * offsetAmount * heat * 3.0;
			vec2 sampleUV = clamp(uv + offset, 0.0, 1.0);
			vec4 c0 = IMG_NORM_PIXEL(inputImage, sampleUV);
			vec4 c1 = IMG_NORM_PIXEL(delay1, sampleUV);
			vec4 c2 = IMG_NORM_PIXEL(delay2, sampleUV);
			vec4 c3 = IMG_NORM_PIXEL(delay3, sampleUV);
			vec4 c4 = IMG_NORM_PIXEL(delay4, sampleUV);
			vec4 result = c0;
			if (heat > 0.8)	{
				result = c4;
			}
			else if (heat > 0.6)	{
				result = c3;
			}
			else if (heat > 0.4)	{
				result = c2;
			}
			else if (heat > 0.2)	{
				result = c1;
			}
			float levels = mix(256.0, 6.0, posterize * heat);
			result.rgb = floor(result.rgb * levels) / levels;
			result.rgb = mix(result.rgb, heatColor(heat).rgb, colorHeat * heat);
			result.a = inputPixel.a;
			gl_FragColor = result;
		}
	}
}
'''


class IsfCvMotionHeatDatamosh(GlslEffect):
    name = 'isf_cv_motion_heat_datamosh'
    label = 'CV Motion Heat Datamosh'
    description = 'Motion heat selects delayed frames, block offsets, and quantization'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.35
    events = ('resetNow',)
    params = effect_params({
        "heat_gain": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 4.0,
            "x-ui": {
                "label": "Heat Gain",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "heatGain"
            }
        },
        "heat_hold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.88,
            "x-ui": {
                "label": "Heat Hold",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "heatHold"
            }
        },
        "block_size": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 96.0,
            "default": 18.0,
            "x-ui": {
                "label": "Block Size",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "blockSize"
            }
        },
        "offset_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.35,
            "x-ui": {
                "label": "Offset Amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "offsetAmount"
            }
        },
        "posterize": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.35,
            "x-ui": {
                "label": "Posterize",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "posterize"
            }
        },
        "color_heat": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.25,
            "x-ui": {
                "label": "Color Heat",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "colorHeat"
            }
        },
        "show_heat": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Show Heat"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "showHeat"
            }
        }
    })
