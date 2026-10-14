"""CV Motion RGB Tear: Motion-gated RGB tearing from temporal difference and local flow

Source: https://github.com/daitomanabe/isf-shaders-for-vj/blob/HEAD/shaders/CV%20Motion%20RGB%20Tear.fs
Author: by daito / Codex
License: MIT
license_class: permissive
Imported verbatim from GLSL (daitomanabe/isf-shaders-for-vj); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository daitomanabe/isf-shaders-for-vj LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"DESCRIPTION": "Motion-gated RGB tearing from temporal difference and local flow",
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
			"NAME": "tearAmount",
			"LABEL": "Tear Amount",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 2.0,
			"DEFAULT": 0.55
		},
		{
			"NAME": "flowGain",
			"LABEL": "Flow Gain",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 20.0,
			"DEFAULT": 7.0
		},
		{
			"NAME": "history",
			"LABEL": "History",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.82
		},
		{
			"NAME": "scanlineMix",
			"LABEL": "Scanline Mix",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.35
		},
		{
			"NAME": "motionThreshold",
			"LABEL": "Motion Threshold",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.04
		},
		{
			"NAME": "resetNow",
			"LABEL": "Reset",
			"TYPE": "event"
		},
		{
			"NAME": "showMask",
			"LABEL": "Show Mask",
			"TYPE": "bool",
			"DEFAULT": false
		}
	],
	"PASSES": [
		{
			"TARGET": "motionBuffer",
			"PERSISTENT": true,
			"FLOAT": true
		},
		{
			"TARGET": "lastFrameBuffer",
			"PERSISTENT": true
		},
		{
			"TARGET": "tearBuffer",
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

vec2 encodeFlow(vec2 flow)
{
	return clamp(flow, -0.5, 0.5) + vec2(0.5);
}

vec2 decodeFlow(vec4 pixel)
{
	return pixel.rg - vec2(0.5);
}

void main()
{
	vec2 uv = isf_FragNormCoord.xy;
	bool doReset = ((FRAMEINDEX <= 1) || resetNow);
	vec4 inputPixel = IMG_THIS_NORM_PIXEL(inputImage);
	
	if (PASSINDEX == 0)	{
		if (doReset)	{
			gl_FragColor = vec4(0.5, 0.5, 0.0, 1.0);
		}
		else	{
			vec2 r = vec2(2.0) / RENDERSIZE.xy;
			float cur = luma(inputPixel);
			float prev = luma(IMG_NORM_PIXEL(lastFrameBuffer, uv));
			float gx = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(r.x, 0.0), 0.0, 1.0))) - luma(IMG_NORM_PIXEL(inputImage, clamp(uv - vec2(r.x, 0.0), 0.0, 1.0)));
			float gy = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(0.0, r.y), 0.0, 1.0))) - luma(IMG_NORM_PIXEL(inputImage, clamp(uv - vec2(0.0, r.y), 0.0, 1.0)));
			float dt = prev - cur;
			vec2 flow = vec2(gx, gy) * dt * flowGain / (gx * gx + gy * gy + 0.08);
			float motion = clamp(abs(dt) * flowGain + length(flow), 0.0, 1.0);
			vec4 oldMotion = IMG_THIS_NORM_PIXEL(motionBuffer);
			flow = mix(flow, decodeFlow(oldMotion), history);
			motion = mix(motion, oldMotion.b, history);
			gl_FragColor = vec4(encodeFlow(flow), motion, 1.0);
		}
	}
	else if (PASSINDEX == 1)	{
		gl_FragColor = inputPixel;
	}
	else if (PASSINDEX == 2)	{
		if (doReset)	{
			gl_FragColor = inputPixel;
		}
		else	{
			vec4 motionPixel = IMG_THIS_NORM_PIXEL(motionBuffer);
			vec2 flow = decodeFlow(motionPixel);
			float active = smoothstep(motionThreshold, motionThreshold + 0.25, motionPixel.b);
			float row = floor(uv.y * RENDERSIZE.y / 6.0);
			float rowRand = rand(vec2(row, floor(TIME * 24.0)));
			vec2 rowSlip = vec2((rowRand - 0.5) * scanlineMix * active, 0.0);
			vec2 rUV = clamp(uv + flow * tearAmount + rowSlip, 0.0, 1.0);
			vec2 gUV = clamp(uv - flow.yx * tearAmount * 0.5, 0.0, 1.0);
			vec2 bUV = clamp(uv - flow * tearAmount - rowSlip, 0.0, 1.0);
			vec4 torn;
			torn.r = IMG_NORM_PIXEL(tearBuffer, rUV).r;
			torn.g = IMG_NORM_PIXEL(inputImage, gUV).g;
			torn.b = IMG_NORM_PIXEL(tearBuffer, bUV).b;
			torn.a = inputPixel.a;
			gl_FragColor = mix(inputPixel, torn, active);
		}
	}
	else	{
		if (showMask)	{
			gl_FragColor = vec4(vec3(IMG_THIS_NORM_PIXEL(motionBuffer).b), 1.0);
		}
		else	{
			gl_FragColor = IMG_THIS_NORM_PIXEL(tearBuffer);
		}
	}
}
'''


class IsfCvMotionRgbTear(GlslEffect):
    name = 'isf_cv_motion_rgb_tear'
    label = 'CV Motion RGB Tear'
    description = 'Motion-gated RGB tearing from temporal difference and local flow'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.35
    events = ('resetNow',)
    params = effect_params({
        "tear_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.55,
            "x-ui": {
                "label": "Tear Amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "tearAmount"
            }
        },
        "flow_gain": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 20.0,
            "default": 7.0,
            "x-ui": {
                "label": "Flow Gain",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flowGain"
            }
        },
        "history": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.82,
            "x-ui": {
                "label": "History",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "history"
            }
        },
        "scanline_mix": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.35,
            "x-ui": {
                "label": "Scanline Mix",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "scanlineMix"
            }
        },
        "motion_threshold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.04,
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
        "show_mask": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Show Mask"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "showMask"
            }
        }
    })
