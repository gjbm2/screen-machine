"""CV Edge Flow Pixel Sort: Pixel sorting driven by motion flow and Sobel edge strength

Source: https://github.com/daitomanabe/isf-shaders-for-vj/blob/HEAD/shaders/CV%20Edge%20Flow%20Pixel%20Sort.fs
Author: by daito / Codex
License: MIT
license_class: permissive
Imported verbatim from GLSL (daitomanabe/isf-shaders-for-vj); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository daitomanabe/isf-shaders-for-vj LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"DESCRIPTION": "Pixel sorting driven by motion flow and Sobel edge strength",
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
			"NAME": "sortAmount",
			"LABEL": "Sort Amount",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.72
		},
		{
			"NAME": "adaptRate",
			"LABEL": "Adapt Rate",
			"TYPE": "float",
			"MIN": 0.0,
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
			"NAME": "edgeThreshold",
			"LABEL": "Edge Threshold",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.16
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
			"NAME": "verticalBias",
			"LABEL": "Vertical Bias",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.35
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
			"TARGET": "sortBuffer",
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

float edgeAt(vec2 uv)
{
	vec2 p = 1.0 / RENDERSIZE.xy;
	float tl = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(-p.x, p.y), 0.0, 1.0)));
	float tc = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(0.0, p.y), 0.0, 1.0)));
	float tr = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(p.x, p.y), 0.0, 1.0)));
	float ml = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(-p.x, 0.0), 0.0, 1.0)));
	float mr = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(p.x, 0.0), 0.0, 1.0)));
	float bl = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(-p.x, -p.y), 0.0, 1.0)));
	float bc = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(0.0, -p.y), 0.0, 1.0)));
	float br = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(p.x, -p.y), 0.0, 1.0)));
	float gx = -tl - 2.0 * ml - bl + tr + 2.0 * mr + br;
	float gy = tl + 2.0 * tc + tr - bl - 2.0 * bc - br;
	return clamp(sqrt(gx * gx + gy * gy), 0.0, 1.0);
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
			flow = mix(flow, decodeFlow(oldFlow), 0.82);
			mag = mix(mag, oldFlow.b, 0.72);
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
			vec4 base = mix(inputPixel, IMG_THIS_NORM_PIXEL(sortBuffer), adaptRate);
			vec4 flowPixel = IMG_THIS_NORM_PIXEL(flowBuffer);
			vec2 flow = decodeFlow(flowPixel);
			float edge = edgeAt(uv);
			float active = smoothstep(edgeThreshold, edgeThreshold + 0.25, edge) * smoothstep(motionThreshold, motionThreshold + 0.25, flowPixel.b);
			vec2 p = 1.0 / RENDERSIZE.xy;
			vec2 dir = (abs(flow.y) > abs(flow.x) * (1.0 - verticalBias)) ? vec2(0.0, sign(flow.y) * p.y) : vec2(sign(flow.x) * p.x, 0.0);
			vec4 neighbor = IMG_NORM_PIXEL(sortBuffer, clamp(uv + dir, 0.0, 1.0));
			float b0 = luma(base);
			float b1 = luma(neighbor);
			vec4 sorted = (b0 < b1) ? neighbor : base;
			gl_FragColor = mix(base, sorted, sortAmount * active);
		}
	}
	else	{
		gl_FragColor = IMG_THIS_NORM_PIXEL(sortBuffer);
	}
}
'''


class IsfCvEdgeFlowPixelSort(GlslEffect):
    name = 'isf_cv_edge_flow_pixel_sort'
    label = 'CV Edge Flow Pixel Sort'
    description = 'Pixel sorting driven by motion flow and Sobel edge strength'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.35
    events = ('resetNow',)
    params = effect_params({
        "sort_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.72,
            "x-ui": {
                "label": "Sort Amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "sortAmount"
            }
        },
        "adapt_rate": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.18,
            "x-ui": {
                "label": "Adapt Rate",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "adaptRate"
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
        "edge_threshold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.16,
            "x-ui": {
                "label": "Edge Threshold",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "edgeThreshold"
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
        },
        "vertical_bias": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.35,
            "x-ui": {
                "label": "Vertical Bias",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "verticalBias"
            }
        }
    })
