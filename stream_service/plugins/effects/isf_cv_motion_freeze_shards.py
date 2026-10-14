"""CV Motion Freeze Shards: Motion-triggered frozen shards that update only where the image changes

Source: https://github.com/daitomanabe/isf-shaders-for-vj/blob/HEAD/shaders/CV%20Motion%20Freeze%20Shards.fs
Author: by daito / Codex
License: MIT
license_class: permissive
Imported verbatim from GLSL (daitomanabe/isf-shaders-for-vj); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository daitomanabe/isf-shaders-for-vj LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"DESCRIPTION": "Motion-triggered frozen shards that update only where the image changes",
	"CREDIT": "by daito / Codex",
	"ISFVSN": "2",
	"CATEGORIES": [
		"Glitch",
		"Stylize"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "shardSize",
			"LABEL": "Shard Size",
			"TYPE": "float",
			"MIN": 0.01,
			"MAX": 0.25,
			"DEFAULT": 0.06
		},
		{
			"NAME": "updateRate",
			"LABEL": "Update Rate",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.55
		},
		{
			"NAME": "motionGain",
			"LABEL": "Motion Gain",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 10.0,
			"DEFAULT": 4.0
		},
		{
			"NAME": "motionThreshold",
			"LABEL": "Motion Threshold",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.08
		},
		{
			"NAME": "slide",
			"LABEL": "Shard Slide",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.22
		},
		{
			"NAME": "blend",
			"LABEL": "Blend",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.18
		},
		{
			"NAME": "resetNow",
			"LABEL": "Reset",
			"TYPE": "event"
		}
	],
	"PASSES": [
		{
			"TARGET": "shardBuffer",
			"PERSISTENT": true
		},
		{
			"TARGET": "lastFrameBuffer",
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

void main()
{
	vec2 uv = isf_FragNormCoord.xy;
	bool doReset = ((FRAMEINDEX <= 1) || resetNow);
	vec4 inputPixel = IMG_THIS_NORM_PIXEL(inputImage);
	
	if (PASSINDEX == 0)	{
		if (doReset)	{
			gl_FragColor = inputPixel;
		}
		else	{
			vec4 oldPixel = IMG_THIS_NORM_PIXEL(shardBuffer);
			vec4 lastPixel = IMG_NORM_PIXEL(lastFrameBuffer, uv);
			float motion = abs(luma(inputPixel) - luma(lastPixel)) * motionGain;
			float active = smoothstep(motionThreshold, motionThreshold + 0.35, motion);
			vec2 cell = floor(uv / shardSize);
			vec2 local = fract(uv / shardSize);
			float diagonal = step(local.x, local.y + rand(cell) * 0.5 - 0.25);
			float choose = step(rand(cell + vec2(floor(TIME * 12.0), diagonal)), updateRate * active);
			vec2 offset = (vec2(rand(cell + vec2(1.7, TIME)), rand(cell + vec2(TIME, 9.2))) - vec2(0.5)) * shardSize * slide * active;
			vec4 fresh = IMG_NORM_PIXEL(inputImage, clamp(uv + offset, 0.0, 1.0));
			vec4 result = mix(oldPixel, fresh, max(choose, blend * active));
			result.a = inputPixel.a;
			gl_FragColor = result;
		}
	}
	else if (PASSINDEX == 1)	{
		gl_FragColor = inputPixel;
	}
	else	{
		gl_FragColor = IMG_THIS_NORM_PIXEL(shardBuffer);
	}
}
'''


class IsfCvMotionFreezeShards(GlslEffect):
    name = 'isf_cv_motion_freeze_shards'
    label = 'CV Motion Freeze Shards'
    description = 'Motion-triggered frozen shards that update only where the image changes'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.35
    events = ('resetNow',)
    params = effect_params({
        "shard_size": {
            "type": "number",
            "minimum": 0.01,
            "maximum": 0.25,
            "default": 0.06,
            "x-ui": {
                "label": "Shard Size",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "shardSize"
            }
        },
        "update_rate": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.55,
            "x-ui": {
                "label": "Update Rate",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "updateRate"
            }
        },
        "motion_gain": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 4.0,
            "x-ui": {
                "label": "Motion Gain",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "motionGain"
            }
        },
        "motion_threshold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.08,
            "x-ui": {
                "label": "Motion Threshold",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "motionThreshold"
            }
        },
        "slide": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.22,
            "x-ui": {
                "label": "Shard Slide",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "slide"
            }
        },
        "blend": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.18,
            "x-ui": {
                "label": "Blend",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "blend"
            }
        }
    })
