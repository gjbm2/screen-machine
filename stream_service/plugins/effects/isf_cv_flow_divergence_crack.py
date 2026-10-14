"""CV Flow Divergence Crack: Cracks and splits the image where estimated flow diverges or converges

Source: https://github.com/daitomanabe/isf-shaders-for-vj/blob/HEAD/shaders/CV%20Flow%20Divergence%20Crack.fs
Author: by daito / Codex
License: MIT
license_class: permissive
Imported verbatim from GLSL (daitomanabe/isf-shaders-for-vj); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository daitomanabe/isf-shaders-for-vj LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"DESCRIPTION": "Cracks and splits the image where estimated flow diverges or converges",
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
			"NAME": "crackAmount",
			"LABEL": "Crack Amount",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 2.0,
			"DEFAULT": 0.7
		},
		{
			"NAME": "crackHold",
			"LABEL": "Crack Hold",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.92
		},
		{
			"NAME": "flowGain",
			"LABEL": "Flow Gain",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 20.0,
			"DEFAULT": 8.0
		},
		{
			"NAME": "lineWidth",
			"LABEL": "Line Width",
			"TYPE": "float",
			"MIN": 0.5,
			"MAX": 6.0,
			"DEFAULT": 1.5
		},
		{
			"NAME": "threshold",
			"LABEL": "Threshold",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.12
		},
		{
			"NAME": "invert",
			"LABEL": "Invert",
			"TYPE": "bool",
			"DEFAULT": false
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
			"TARGET": "crackBuffer",
			"PERSISTENT": true,
			"FLOAT": true
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
			gx += luma(IMG_NORM_PIXEL(lastFrameBuffer, clamp(uv + vec2(r.x, 0.0), 0.0, 1.0))) - luma(IMG_NORM_PIXEL(lastFrameBuffer, clamp(uv - vec2(r.x, 0.0), 0.0, 1.0)));
			float gy = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(0.0, r.y), 0.0, 1.0))) - luma(IMG_NORM_PIXEL(inputImage, clamp(uv - vec2(0.0, r.y), 0.0, 1.0)));
			gy += luma(IMG_NORM_PIXEL(lastFrameBuffer, clamp(uv + vec2(0.0, r.y), 0.0, 1.0))) - luma(IMG_NORM_PIXEL(lastFrameBuffer, clamp(uv - vec2(0.0, r.y), 0.0, 1.0)));
			float dt = prev - cur;
			vec2 flow = vec2(gx, gy) * dt * flowGain / (gx * gx + gy * gy + 0.08);
			float mag = clamp(abs(dt) * flowGain + length(flow), 0.0, 1.0);
			vec4 oldFlow = IMG_THIS_NORM_PIXEL(flowBuffer);
			flow = mix(flow, decodeFlow(oldFlow), 0.88);
			mag = mix(mag, oldFlow.b, 0.82);
			gl_FragColor = vec4(encodeFlow(flow), mag, 1.0);
		}
	}
	else if (PASSINDEX == 1)	{
		gl_FragColor = inputPixel;
	}
	else if (PASSINDEX == 2)	{
		if (doReset)	{
			gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);
		}
		else	{
			vec2 p = lineWidth / RENDERSIZE.xy;
			vec2 fL = decodeFlow(IMG_NORM_PIXEL(flowBuffer, clamp(uv - vec2(p.x, 0.0), 0.0, 1.0)));
			vec2 fR = decodeFlow(IMG_NORM_PIXEL(flowBuffer, clamp(uv + vec2(p.x, 0.0), 0.0, 1.0)));
			vec2 fB = decodeFlow(IMG_NORM_PIXEL(flowBuffer, clamp(uv - vec2(0.0, p.y), 0.0, 1.0)));
			vec2 fT = decodeFlow(IMG_NORM_PIXEL(flowBuffer, clamp(uv + vec2(0.0, p.y), 0.0, 1.0)));
			float divergence = (fR.x - fL.x) + (fT.y - fB.y);
			float crack = smoothstep(threshold, threshold + 0.3, abs(divergence) * 8.0);
			float oldCrack = IMG_THIS_NORM_PIXEL(crackBuffer).r;
			crack = max(crack, oldCrack * crackHold);
			gl_FragColor = vec4(crack, divergence * 0.5 + 0.5, 0.0, 1.0);
		}
	}
	else	{
		vec4 crackPixel = IMG_THIS_NORM_PIXEL(crackBuffer);
		vec2 flow = decodeFlow(IMG_THIS_NORM_PIXEL(flowBuffer));
		float crack = crackPixel.r;
		vec2 split = normalize(flow + vec2(crackPixel.g - 0.5, 0.001)) * crack * crackAmount * 0.08;
		vec4 a = IMG_NORM_PIXEL(inputImage, clamp(uv + split, 0.0, 1.0));
		vec4 b = IMG_NORM_PIXEL(inputImage, clamp(uv - split, 0.0, 1.0));
		vec4 result = mix(inputPixel, mix(a, b, 0.5), crack);
		vec3 lineColor = invert ? vec3(1.0) : vec3(0.0);
		result.rgb = mix(result.rgb, lineColor, crack * crack * 0.65);
		result.a = inputPixel.a;
		gl_FragColor = result;
	}
}
'''


class IsfCvFlowDivergenceCrack(GlslEffect):
    name = 'isf_cv_flow_divergence_crack'
    label = 'CV Flow Divergence Crack'
    description = 'Cracks and splits the image where estimated flow diverges or converges'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.35
    events = ('resetNow',)
    params = effect_params({
        "crack_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.7,
            "x-ui": {
                "label": "Crack Amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "crackAmount"
            }
        },
        "crack_hold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.92,
            "x-ui": {
                "label": "Crack Hold",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "crackHold"
            }
        },
        "flow_gain": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 20.0,
            "default": 8.0,
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
        "line_width": {
            "type": "number",
            "minimum": 0.5,
            "maximum": 6.0,
            "default": 1.5,
            "x-ui": {
                "label": "Line Width",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "lineWidth"
            }
        },
        "threshold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.12,
            "x-ui": {
                "label": "Threshold",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "threshold"
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
        }
    })
