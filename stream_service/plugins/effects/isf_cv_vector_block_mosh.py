"""CV Vector Block Mosh: Block-based motion vector mosh with packet-style jitter

Source: https://github.com/daitomanabe/isf-shaders-for-vj/blob/HEAD/shaders/CV%20Vector%20Block%20Mosh.fs
Author: by daito / Codex
License: MIT
license_class: permissive
Imported verbatim from GLSL (daitomanabe/isf-shaders-for-vj); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository daitomanabe/isf-shaders-for-vj LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"DESCRIPTION": "Block-based motion vector mosh with packet-style jitter",
	"CREDIT": "by daito / Codex",
	"ISFVSN": "2",
	"CATEGORIES": [
		"Glitch",
		"Distortion Effect"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "moshAmount",
			"LABEL": "Mosh Amount",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 3.0,
			"DEFAULT": 1.1
		},
		{
			"NAME": "hold",
			"LABEL": "Hold",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.94
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
			"NAME": "blockSize",
			"LABEL": "Block Size",
			"TYPE": "float",
			"MIN": 2.0,
			"MAX": 128.0,
			"DEFAULT": 24.0
		},
		{
			"NAME": "packetDrop",
			"LABEL": "Packet Drop",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.22
		},
		{
			"NAME": "jitter",
			"LABEL": "Jitter",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.22
		},
		{
			"NAME": "flowHold",
			"LABEL": "Flow Hold",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.9
		},
		{
			"NAME": "noiseRemoval",
			"LABEL": "Noise Removal",
			"TYPE": "float",
			"MIN": 0.001,
			"MAX": 1.0,
			"DEFAULT": 0.08
		},
		{
			"NAME": "motionThreshold",
			"LABEL": "Motion Threshold",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.03
		},
		{
			"NAME": "resetNow",
			"LABEL": "Reset",
			"TYPE": "event"
		}
	],
	"PASSES": [
		{
			"TARGET": "flowBuffer",
			"PERSISTENT": true,
			"FLOAT": true
		},
		{
			"TARGET": "lastFrameBuffer",
			"PERSISTENT": true
		},
		{
			"TARGET": "moshBuffer",
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

vec2 blockCenter(vec2 uv)
{
	vec2 block = max(vec2(blockSize) / RENDERSIZE.xy, vec2(1.0) / RENDERSIZE.xy);
	return clamp((floor(uv / block) + vec2(0.5)) * block, 0.0, 1.0);
}

vec4 flowAt(vec2 uv)
{
	vec2 r = vec2(2.0) / RENDERSIZE.xy;
	float cur = luma(IMG_NORM_PIXEL(inputImage, uv));
	float prev = luma(IMG_NORM_PIXEL(lastFrameBuffer, uv));
	float gx = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(r.x, 0.0), 0.0, 1.0))) - luma(IMG_NORM_PIXEL(inputImage, clamp(uv - vec2(r.x, 0.0), 0.0, 1.0)));
	gx += luma(IMG_NORM_PIXEL(lastFrameBuffer, clamp(uv + vec2(r.x, 0.0), 0.0, 1.0))) - luma(IMG_NORM_PIXEL(lastFrameBuffer, clamp(uv - vec2(r.x, 0.0), 0.0, 1.0)));
	float gy = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(0.0, r.y), 0.0, 1.0))) - luma(IMG_NORM_PIXEL(inputImage, clamp(uv - vec2(0.0, r.y), 0.0, 1.0)));
	gy += luma(IMG_NORM_PIXEL(lastFrameBuffer, clamp(uv + vec2(0.0, r.y), 0.0, 1.0))) - luma(IMG_NORM_PIXEL(lastFrameBuffer, clamp(uv - vec2(0.0, r.y), 0.0, 1.0)));
	float dt = prev - cur;
	float denom = gx * gx + gy * gy + noiseRemoval;
	vec2 flow = vec2(gx, gy) * dt * flowGain / denom;
	float mag = clamp(abs(dt) * flowGain + length(flow), 0.0, 1.0);
	flow *= smoothstep(motionThreshold, motionThreshold + 0.25, mag);
	return vec4(encodeFlow(flow), mag, 1.0);
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
			vec4 freshFlow = flowAt(uv);
			vec4 oldFlow = IMG_THIS_NORM_PIXEL(flowBuffer);
			vec2 flow = mix(decodeFlow(freshFlow), decodeFlow(oldFlow), flowHold);
			float mag = mix(freshFlow.b, oldFlow.b, flowHold);
			gl_FragColor = vec4(encodeFlow(flow), mag, 1.0);
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
			vec2 block = max(vec2(blockSize) / RENDERSIZE.xy, vec2(1.0) / RENDERSIZE.xy);
			vec2 blockId = floor(uv / block);
			vec4 flowPixel = IMG_NORM_PIXEL(flowBuffer, blockCenter(uv));
			vec2 flow = decodeFlow(flowPixel);
			float active = smoothstep(motionThreshold, motionThreshold + 0.25, flowPixel.b);
			float drop = step(rand(blockId + vec2(floor(TIME * 18.0), 4.7)), packetDrop);
			vec2 jitterVec = (vec2(rand(blockId + vec2(FRAMEINDEX, 1.3)), rand(blockId + vec2(8.1, FRAMEINDEX))) - vec2(0.5)) * block * jitter;
			vec2 sampleUV = clamp(uv - flow * moshAmount * mix(1.0, 3.0, drop) + jitterVec * active, 0.0, 1.0);
			vec4 mosh = IMG_NORM_PIXEL(moshBuffer, sampleUV);
			vec4 result = mix(inputPixel, mosh, hold * max(active, drop));
			result.a = inputPixel.a;
			gl_FragColor = result;
		}
	}
	else	{
		gl_FragColor = IMG_THIS_NORM_PIXEL(moshBuffer);
	}
}
'''


class IsfCvVectorBlockMosh(GlslEffect):
    name = 'isf_cv_vector_block_mosh'
    label = 'CV Vector Block Mosh'
    description = 'Block-based motion vector mosh with packet-style jitter'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.4
    events = ('resetNow',)
    params = effect_params({
        "mosh_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 3.0,
            "default": 1.1,
            "x-ui": {
                "label": "Mosh Amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "moshAmount"
            }
        },
        "hold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.94,
            "x-ui": {
                "label": "Hold",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "hold"
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
        "block_size": {
            "type": "number",
            "minimum": 2.0,
            "maximum": 128.0,
            "default": 24.0,
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
        "packet_drop": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.22,
            "x-ui": {
                "label": "Packet Drop",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "packetDrop"
            }
        },
        "jitter": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.22,
            "x-ui": {
                "label": "Jitter",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "jitter"
            }
        },
        "flow_hold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.9,
            "x-ui": {
                "label": "Flow Hold",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flowHold"
            }
        },
        "noise_removal": {
            "type": "number",
            "minimum": 0.001,
            "maximum": 1.0,
            "default": 0.08,
            "x-ui": {
                "label": "Noise Removal",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "noiseRemoval"
            }
        },
        "motion_threshold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.03,
            "x-ui": {
                "label": "Motion Threshold",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "motionThreshold"
            }
        }
    })
