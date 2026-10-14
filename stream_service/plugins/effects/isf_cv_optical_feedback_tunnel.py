"""CV Optical Feedback Tunnel: Motion-vector feedback tunnel with flow-reactive pull

Source: https://github.com/daitomanabe/isf-shaders-for-vj/blob/HEAD/shaders/CV%20Optical%20Feedback%20Tunnel.fs
Author: by daito / Codex
License: MIT
license_class: permissive
Imported verbatim from GLSL (daitomanabe/isf-shaders-for-vj); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository daitomanabe/isf-shaders-for-vj LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"DESCRIPTION": "Motion-vector feedback tunnel with flow-reactive pull",
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
			"NAME": "tunnelAmount",
			"LABEL": "Tunnel Amount",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.35
		},
		{
			"NAME": "flowAmount",
			"LABEL": "Flow Amount",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 2.0,
			"DEFAULT": 0.75
		},
		{
			"NAME": "feedback",
			"LABEL": "Feedback",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.93
		},
		{
			"NAME": "freshMix",
			"LABEL": "Fresh Mix",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.12
		},
		{
			"NAME": "rotate",
			"LABEL": "Rotate",
			"TYPE": "float",
			"MIN": -1.0,
			"MAX": 1.0,
			"DEFAULT": 0.18
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
			"TARGET": "feedbackBuffer",
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
			float mag = clamp(abs(dt) * flowGain + length(flow), 0.0, 1.0);
			vec4 oldFlow = IMG_THIS_NORM_PIXEL(flowBuffer);
			flow = mix(flow, decodeFlow(oldFlow), 0.9);
			mag = mix(mag, oldFlow.b, 0.78);
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
			float mag = flowPixel.b;
			vec2 p = uv - vec2(0.5);
			float a = rotate * 0.035 + mag * rotate * 0.08;
			mat2 rot = mat2(cos(a), -sin(a), sin(a), cos(a));
			vec2 tunnelUV = vec2(0.5) + rot * p * (1.0 - tunnelAmount * 0.035) - flow * flowAmount;
			tunnelUV = clamp(tunnelUV, 0.0, 1.0);
			vec4 fb = IMG_NORM_PIXEL(feedbackBuffer, tunnelUV);
			vec4 result = mix(inputPixel, fb, feedback);
			result = mix(result, inputPixel, freshMix * smoothstep(0.0, 0.5, mag));
			result.a = inputPixel.a;
			gl_FragColor = result;
		}
	}
	else	{
		gl_FragColor = IMG_THIS_NORM_PIXEL(feedbackBuffer);
	}
}
'''


class IsfCvOpticalFeedbackTunnel(GlslEffect):
    name = 'isf_cv_optical_feedback_tunnel'
    label = 'CV Optical Feedback Tunnel'
    description = 'Motion-vector feedback tunnel with flow-reactive pull'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.45
    events = ('resetNow',)
    params = effect_params({
        "tunnel_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.35,
            "x-ui": {
                "label": "Tunnel Amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "tunnelAmount"
            }
        },
        "flow_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.75,
            "x-ui": {
                "label": "Flow Amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flowAmount"
            }
        },
        "feedback": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.93,
            "x-ui": {
                "label": "Feedback",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "feedback"
            }
        },
        "fresh_mix": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.12,
            "x-ui": {
                "label": "Fresh Mix",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "freshMix"
            }
        },
        "rotate": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": 0.18,
            "x-ui": {
                "label": "Rotate",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "rotate"
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
        }
    })
