"""CV Saliency Warp: Warp salient regions detected from edge, saturation, and motion

Source: https://github.com/daitomanabe/isf-shaders-for-vj/blob/HEAD/shaders/CV%20Saliency%20Warp.fs
Author: by daito / Codex
License: MIT
license_class: permissive
Imported verbatim from GLSL (daitomanabe/isf-shaders-for-vj); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository daitomanabe/isf-shaders-for-vj LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"DESCRIPTION": "Warp salient regions detected from edge, saturation, and motion",
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
			"NAME": "warpAmount",
			"LABEL": "Warp Amount",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.5,
			"DEFAULT": 0.45
		},
		{
			"NAME": "edgeWeight",
			"LABEL": "Edge Weight",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 2.0,
			"DEFAULT": 0.8
		},
		{
			"NAME": "motionWeight",
			"LABEL": "Motion Weight",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 2.0,
			"DEFAULT": 0.9
		},
		{
			"NAME": "chromaWeight",
			"LABEL": "Chroma Weight",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 2.0,
			"DEFAULT": 0.55
		},
		{
			"NAME": "saliencyHold",
			"LABEL": "Saliency Hold",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.75
		},
		{
			"NAME": "threshold",
			"LABEL": "Threshold",
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
			"NAME": "showMask",
			"LABEL": "Show Mask",
			"TYPE": "bool",
			"DEFAULT": false
		}
	],
	"PASSES": [
		{
			"TARGET": "saliencyBuffer",
			"PERSISTENT": true,
			"FLOAT": true
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

float luma(vec4 color)
{
	return dot(color.rgb, lumaCoeffs);
}

float saturation(vec4 color)
{
	float mx = max(max(color.r, color.g), color.b);
	float mn = min(min(color.r, color.g), color.b);
	return mx - mn;
}

float edgeAt(vec2 uv)
{
	vec2 p = 1.0 / RENDERSIZE.xy;
	float l = luma(IMG_NORM_PIXEL(inputImage, clamp(uv - vec2(p.x, 0.0), 0.0, 1.0)));
	float r = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(p.x, 0.0), 0.0, 1.0)));
	float b = luma(IMG_NORM_PIXEL(inputImage, clamp(uv - vec2(0.0, p.y), 0.0, 1.0)));
	float t = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(0.0, p.y), 0.0, 1.0)));
	return clamp(length(vec2(r - l, t - b)) * 2.0, 0.0, 1.0);
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
			vec4 lastPixel = IMG_NORM_PIXEL(lastFrameBuffer, uv);
			float motion = length(inputPixel.rgb - lastPixel.rgb);
			float score = edgeAt(uv) * edgeWeight + motion * motionWeight + saturation(inputPixel) * chromaWeight;
			score = smoothstep(threshold, threshold + 0.6, score);
			float oldScore = IMG_THIS_NORM_PIXEL(saliencyBuffer).r;
			score = mix(score, oldScore, saliencyHold);
			gl_FragColor = vec4(score, score, score, 1.0);
		}
	}
	else if (PASSINDEX == 1)	{
		gl_FragColor = inputPixel;
	}
	else	{
		float s = IMG_THIS_NORM_PIXEL(saliencyBuffer).r;
		if (showMask)	{
			gl_FragColor = vec4(vec3(s), 1.0);
		}
		else	{
			vec2 p = 1.0 / RENDERSIZE.xy;
			float l = IMG_NORM_PIXEL(saliencyBuffer, clamp(uv - vec2(p.x, 0.0), 0.0, 1.0)).r;
			float r = IMG_NORM_PIXEL(saliencyBuffer, clamp(uv + vec2(p.x, 0.0), 0.0, 1.0)).r;
			float b = IMG_NORM_PIXEL(saliencyBuffer, clamp(uv - vec2(0.0, p.y), 0.0, 1.0)).r;
			float t = IMG_NORM_PIXEL(saliencyBuffer, clamp(uv + vec2(0.0, p.y), 0.0, 1.0)).r;
			vec2 grad = normalize(vec2(r - l, t - b) + vec2(0.0001));
			vec2 sampleUV = clamp(uv + grad * s * warpAmount * 0.08, 0.0, 1.0);
			vec4 warped = IMG_NORM_PIXEL(inputImage, sampleUV);
			gl_FragColor = mix(inputPixel, warped, s);
		}
	}
}
'''


class IsfCvSaliencyWarp(GlslEffect):
    name = 'isf_cv_saliency_warp'
    label = 'CV Saliency Warp'
    description = 'Warp salient regions detected from edge, saturation, and motion'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.35
    events = ('resetNow',)
    params = effect_params({
        "warp_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.5,
            "default": 0.45,
            "x-ui": {
                "label": "Warp Amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "warpAmount"
            }
        },
        "edge_weight": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.8,
            "x-ui": {
                "label": "Edge Weight",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "edgeWeight"
            }
        },
        "motion_weight": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.9,
            "x-ui": {
                "label": "Motion Weight",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "motionWeight"
            }
        },
        "chroma_weight": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.55,
            "x-ui": {
                "label": "Chroma Weight",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "chromaWeight"
            }
        },
        "saliency_hold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.75,
            "x-ui": {
                "label": "Saliency Hold",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "saliencyHold"
            }
        },
        "threshold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.25,
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
