"""CV Flow Smear: Smears moving pixels along estimated optical flow

Source: https://github.com/daitomanabe/isf-shaders-for-vj/blob/HEAD/shaders/CV%20Flow%20Smear.fs
Author: by daito / Codex
License: MIT
license_class: permissive
Imported verbatim from GLSL (daitomanabe/isf-shaders-for-vj); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository daitomanabe/isf-shaders-for-vj LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"DESCRIPTION": "Smears moving pixels along estimated optical flow",
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
			"NAME": "amount",
			"LABEL": "Smear Amount",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 2.5,
			"DEFAULT": 0.8
		},
		{
			"NAME": "flowGain",
			"LABEL": "Flow Gain",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 20.0,
			"DEFAULT": 6.0
		},
		{
			"NAME": "flowHold",
			"LABEL": "Flow Hold",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.86
		},
		{
			"NAME": "frameHold",
			"LABEL": "Frame Hold",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.9
		},
		{
			"NAME": "recovery",
			"LABEL": "Recovery",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.04
		},
		{
			"NAME": "sampleCount",
			"LABEL": "Samples",
			"TYPE": "float",
			"MIN": 1.0,
			"MAX": 8.0,
			"DEFAULT": 5.0
		},
		{
			"NAME": "flowRadius",
			"LABEL": "Flow Radius",
			"TYPE": "float",
			"MIN": 0.5,
			"MAX": 8.0,
			"DEFAULT": 2.0
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
			"DEFAULT": 0.035
		},
		{
			"NAME": "resetNow",
			"LABEL": "Reset",
			"TYPE": "event"
		},
		{
			"NAME": "displayMode",
			"LABEL": "Display",
			"TYPE": "long",
			"VALUES": [
				0,
				1,
				2
			],
			"LABELS": [
				"Smear",
				"Flow",
				"Motion"
			],
			"DEFAULT": 0
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
			"TARGET": "smearBuffer",
			"PERSISTENT": true
		},
		{
		
		}
	]
}*/

const vec3 lumaCoeffs = vec3(0.2126, 0.7152, 0.0722);

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

vec4 flowAt(vec2 uv)
{
	vec2 r = flowRadius / RENDERSIZE.xy;
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
			float mag = mix(freshFlow.b, oldFlow.b, flowHold * 0.75);
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
			vec4 flowPixel = IMG_THIS_NORM_PIXEL(flowBuffer);
			vec2 flow = decodeFlow(flowPixel);
			float active = smoothstep(motionThreshold, motionThreshold + 0.2, flowPixel.b);
			vec4 smear = IMG_THIS_NORM_PIXEL(smearBuffer);
			float total = 1.0;
			for (float i = 1.0; i <= 8.0; i += 1.0)	{
				if (i <= sampleCount)	{
					float t = i / sampleCount;
					vec2 sampleUV = clamp(uv - flow * amount * t, 0.0, 1.0);
					smear += IMG_NORM_PIXEL(smearBuffer, sampleUV);
					total += 1.0;
				}
			}
			smear /= total;
			vec4 result = mix(inputPixel, smear, frameHold * active);
			result = mix(result, inputPixel, recovery * (1.0 - active));
			result.a = inputPixel.a;
			gl_FragColor = result;
		}
	}
	else	{
		if (displayMode == 1)	{
			vec4 flowPixel = IMG_THIS_NORM_PIXEL(flowBuffer);
			gl_FragColor = vec4(flowPixel.r, flowPixel.g, 1.0 - flowPixel.b, 1.0);
		}
		else if (displayMode == 2)	{
			vec4 flowPixel = IMG_THIS_NORM_PIXEL(flowBuffer);
			gl_FragColor = vec4(vec3(flowPixel.b), 1.0);
		}
		else	{
			gl_FragColor = IMG_THIS_NORM_PIXEL(smearBuffer);
		}
	}
}
'''


class IsfCvFlowSmear(GlslEffect):
    name = 'isf_cv_flow_smear'
    label = 'CV Flow Smear'
    description = 'Smears moving pixels along estimated optical flow'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.35
    events = ('resetNow',)
    params = effect_params({
        "p_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.5,
            "default": 0.8,
            "x-ui": {
                "label": "Smear Amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "amount"
            }
        },
        "flow_gain": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 20.0,
            "default": 6.0,
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
        "flow_hold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.86,
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
        "frame_hold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.9,
            "x-ui": {
                "label": "Frame Hold",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "frameHold"
            }
        },
        "recovery": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.04,
            "x-ui": {
                "label": "Recovery",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "recovery"
            }
        },
        "sample_count": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 8.0,
            "default": 5.0,
            "x-ui": {
                "label": "Samples",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "sampleCount"
            }
        },
        "flow_radius": {
            "type": "number",
            "minimum": 0.5,
            "maximum": 8.0,
            "default": 2.0,
            "x-ui": {
                "label": "Flow Radius",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flowRadius"
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
            "default": 0.035,
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
        "display_mode": {
            "type": "integer",
            "enum": [
                0,
                1,
                2
            ],
            "default": 0,
            "x-ui": {
                "label": "Display",
                "widget": "select",
                "labels": [
                    "Smear",
                    "Flow",
                    "Motion"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "displayMode"
            }
        }
    })
