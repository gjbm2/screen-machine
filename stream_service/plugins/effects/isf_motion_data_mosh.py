"""Motion Data Mosh: Optical-flow driven data mosh using persistent motion and frame buffers

Source: https://github.com/daitomanabe/isf-shaders-for-vj/blob/HEAD/shaders/Motion%20Data%20Mosh.fs
Author: by daito / Codex, based on VIDVOX ISF optical flow and mosh patterns
License: MIT
license_class: permissive
Imported verbatim from GLSL (daitomanabe/isf-shaders-for-vj); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository daitomanabe/isf-shaders-for-vj LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"DESCRIPTION": "Optical-flow driven data mosh using persistent motion and frame buffers",
	"CREDIT": "by daito / Codex, based on VIDVOX ISF optical flow and mosh patterns",
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
			"MAX": 2.0,
			"DEFAULT": 0.85
		},
		{
			"NAME": "moshHold",
			"LABEL": "Frame Hold",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.92
		},
		{
			"NAME": "freshMix",
			"LABEL": "Recovery",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.03
		},
		{
			"NAME": "flowScale",
			"LABEL": "Flow Gain",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 20.0,
			"DEFAULT": 6.0
		},
		{
			"NAME": "flowPersistence",
			"LABEL": "Flow Persistence",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.88
		},
		{
			"NAME": "flowOffset",
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
			"NAME": "blockSize",
			"LABEL": "Block Size",
			"TYPE": "float",
			"MIN": 1.0,
			"MAX": 96.0,
			"DEFAULT": 18.0
		},
		{
			"NAME": "blockJitter",
			"LABEL": "Block Jitter",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.16
		},
		{
			"NAME": "chromaSlip",
			"LABEL": "RGB Slip",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.22
		},
		{
			"NAME": "edgeInjection",
			"LABEL": "Edge Inject",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.18
		},
		{
			"NAME": "keyframeMix",
			"LABEL": "Keyframe Mix",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.0
		},
		{
			"NAME": "updateKeyframe",
			"LABEL": "Update Keyframe",
			"TYPE": "bool",
			"DEFAULT": false
		},
		{
			"NAME": "takeKeyframe",
			"LABEL": "Take Keyframe",
			"TYPE": "event"
		},
		{
			"NAME": "resetNow",
			"LABEL": "Reset",
			"TYPE": "event"
		},
		{
			"NAME": "invertMotion",
			"LABEL": "Invert Motion",
			"TYPE": "bool",
			"DEFAULT": false
		},
		{
			"NAME": "displayMode",
			"LABEL": "Display",
			"TYPE": "long",
			"VALUES": [
				0,
				1,
				2,
				3
			],
			"LABELS": [
				"Mosh",
				"Flow",
				"Motion Mask",
				"Keyframe"
			],
			"DEFAULT": 0
		}
	],
	"PASSES": [
		{
			"TARGET": "flowBuffer",
			"PERSISTENT": true,
			"FLOAT": true,
			"WIDTH": "floor($WIDTH/2.0)",
			"HEIGHT": "floor($HEIGHT/2.0)"
		},
		{
			"TARGET": "lastFrameBuffer",
			"PERSISTENT": true
		},
		{
			"TARGET": "keyFrameBuffer",
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

varying vec2 left_coord;
varying vec2 right_coord;
varying vec2 above_coord;
varying vec2 below_coord;

varying vec2 lefta_coord;
varying vec2 righta_coord;
varying vec2 leftb_coord;
varying vec2 rightb_coord;

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

vec2 decodeFlow(vec4 flowPixel)
{
	return flowPixel.rg - vec2(0.5);
}

float edgeAmount()
{
	vec4 colorL = IMG_NORM_PIXEL(inputImage, left_coord);
	vec4 colorR = IMG_NORM_PIXEL(inputImage, right_coord);
	vec4 colorA = IMG_NORM_PIXEL(inputImage, above_coord);
	vec4 colorB = IMG_NORM_PIXEL(inputImage, below_coord);
	vec4 colorLA = IMG_NORM_PIXEL(inputImage, lefta_coord);
	vec4 colorRA = IMG_NORM_PIXEL(inputImage, righta_coord);
	vec4 colorLB = IMG_NORM_PIXEL(inputImage, leftb_coord);
	vec4 colorRB = IMG_NORM_PIXEL(inputImage, rightb_coord);
	
	float gx = (-1.0 * luma(colorLA)) + (-2.0 * luma(colorL)) + (-1.0 * luma(colorLB)) + (1.0 * luma(colorRA)) + (2.0 * luma(colorR)) + (1.0 * luma(colorRB));
	float gy = (1.0 * luma(colorLA)) + (2.0 * luma(colorA)) + (1.0 * luma(colorRA)) + (-1.0 * luma(colorLB)) + (-2.0 * luma(colorB)) + (-1.0 * luma(colorRB));
	
	return clamp(sqrt(gx * gx + gy * gy), 0.0, 1.0);
}

vec2 blockCenter(vec2 uv)
{
	vec2 block = max(vec2(blockSize) / RENDERSIZE.xy, vec2(1.0) / RENDERSIZE.xy);
	return clamp((floor(uv / block) + vec2(0.5)) * block, 0.0, 1.0);
}

void main()
{
	vec2 uv = isf_FragNormCoord.xy;
	vec4 inputPixel = IMG_THIS_NORM_PIXEL(inputImage);
	bool doReset = ((FRAMEINDEX <= 1) || resetNow);
	
	if (PASSINDEX == 0)	{
		if (doReset)	{
			gl_FragColor = vec4(0.5, 0.5, 0.0, 1.0);
		}
		else	{
			vec2 radius = flowOffset / RENDERSIZE.xy;
			vec4 cur = IMG_NORM_PIXEL(inputImage, uv);
			vec4 prev = IMG_NORM_PIXEL(lastFrameBuffer, uv);
			
			float curL = luma(cur);
			float prevL = luma(prev);
			float dx = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(radius.x, 0.0), 0.0, 1.0))) - luma(IMG_NORM_PIXEL(inputImage, clamp(uv - vec2(radius.x, 0.0), 0.0, 1.0)));
			dx += luma(IMG_NORM_PIXEL(lastFrameBuffer, clamp(uv + vec2(radius.x, 0.0), 0.0, 1.0))) - luma(IMG_NORM_PIXEL(lastFrameBuffer, clamp(uv - vec2(radius.x, 0.0), 0.0, 1.0)));
			
			float dy = luma(IMG_NORM_PIXEL(inputImage, clamp(uv + vec2(0.0, radius.y), 0.0, 1.0))) - luma(IMG_NORM_PIXEL(inputImage, clamp(uv - vec2(0.0, radius.y), 0.0, 1.0)));
			dy += luma(IMG_NORM_PIXEL(lastFrameBuffer, clamp(uv + vec2(0.0, radius.y), 0.0, 1.0))) - luma(IMG_NORM_PIXEL(lastFrameBuffer, clamp(uv - vec2(0.0, radius.y), 0.0, 1.0)));
			
			float dt = prevL - curL;
			float denom = dx * dx + dy * dy + noiseRemoval;
			vec2 newFlow = vec2(dx, dy) * dt * flowScale / denom;
			float motionMag = clamp(abs(dt) * flowScale + length(newFlow), 0.0, 1.0);
			
			vec4 oldFlowPixel = IMG_THIS_NORM_PIXEL(flowBuffer);
			vec2 oldFlow = decodeFlow(oldFlowPixel);
			float oldMag = oldFlowPixel.b;
			
			vec2 flow = mix(newFlow, oldFlow, flowPersistence);
			float mag = mix(motionMag, oldMag, flowPersistence * 0.75);
			flow *= smoothstep(motionThreshold * 0.5, motionThreshold + 0.25, mag);
			
			gl_FragColor = vec4(encodeFlow(flow), mag, 1.0);
		}
	}
	else if (PASSINDEX == 1)	{
		gl_FragColor = inputPixel;
	}
	else if (PASSINDEX == 2)	{
		vec4 oldKey = IMG_THIS_NORM_PIXEL(keyFrameBuffer);
		if (doReset || updateKeyframe || takeKeyframe)	{
			gl_FragColor = inputPixel;
		}
		else	{
			gl_FragColor = oldKey;
		}
	}
	else if (PASSINDEX == 3)	{
		if (doReset)	{
			gl_FragColor = inputPixel;
		}
		else	{
			vec2 flowUV = blockCenter(uv);
			vec4 flowPixel = IMG_NORM_PIXEL(flowBuffer, flowUV);
			vec2 motion = decodeFlow(flowPixel);
			if (invertMotion)	{
				motion *= -1.0;
			}
			
			float active = smoothstep(motionThreshold, motionThreshold + 0.25, flowPixel.b);
			vec2 block = max(vec2(blockSize) / RENDERSIZE.xy, vec2(1.0) / RENDERSIZE.xy);
			vec2 blockId = floor(uv / block);
			vec2 jitterSeed = vec2(rand(blockId + vec2(FRAMEINDEX, 17.13)), rand(blockId + vec2(9.47, FRAMEINDEX)));
			vec2 jitter = (jitterSeed - vec2(0.5)) * block * blockJitter * active;
			
			vec2 baseUV = clamp(uv - motion * moshAmount + jitter, 0.0, 1.0);
			vec2 rUV = clamp(baseUV + motion * chromaSlip, 0.0, 1.0);
			vec2 bUV = clamp(baseUV - motion * chromaSlip, 0.0, 1.0);
			
			vec4 warped = IMG_NORM_PIXEL(moshBuffer, baseUV);
			if (chromaSlip > 0.0)	{
				warped.r = IMG_NORM_PIXEL(moshBuffer, rUV).r;
				warped.b = IMG_NORM_PIXEL(moshBuffer, bUV).b;
			}
			
			vec4 keyPixel = IMG_NORM_PIXEL(keyFrameBuffer, uv);
			float hold = clamp(moshHold * active, 0.0, 1.0);
			vec4 result = mix(inputPixel, warped, hold);
			result = mix(result, inputPixel, freshMix * (1.0 - active));
			result = mix(result, keyPixel, keyframeMix);
			
			float edge = edgeAmount();
			result = mix(result, inputPixel, clamp(edge * edgeInjection, 0.0, 1.0));
			result.a = inputPixel.a;
			
			gl_FragColor = result;
		}
	}
	else	{
		if (displayMode == 1)	{
			vec4 flowPixel = IMG_NORM_PIXEL(flowBuffer, uv);
			gl_FragColor = vec4(flowPixel.r, flowPixel.g, 1.0 - flowPixel.b, 1.0);
		}
		else if (displayMode == 2)	{
			vec4 flowPixel = IMG_NORM_PIXEL(flowBuffer, uv);
			gl_FragColor = vec4(vec3(flowPixel.b), 1.0);
		}
		else if (displayMode == 3)	{
			gl_FragColor = IMG_THIS_NORM_PIXEL(keyFrameBuffer);
		}
		else	{
			gl_FragColor = IMG_THIS_NORM_PIXEL(moshBuffer);
		}
	}
}
'''

VS = r'''varying vec2 left_coord;
varying vec2 right_coord;
varying vec2 above_coord;
varying vec2 below_coord;

varying vec2 lefta_coord;
varying vec2 righta_coord;
varying vec2 leftb_coord;
varying vec2 rightb_coord;

void main()
{
	isf_vertShaderInit();
	vec2 texc = vec2(isf_FragNormCoord[0], isf_FragNormCoord[1]);
	vec2 d = 1.0 / RENDERSIZE;
	
	left_coord = clamp(vec2(texc.xy + vec2(-d.x, 0.0)), 0.0, 1.0);
	right_coord = clamp(vec2(texc.xy + vec2(d.x, 0.0)), 0.0, 1.0);
	above_coord = clamp(vec2(texc.xy + vec2(0.0, d.y)), 0.0, 1.0);
	below_coord = clamp(vec2(texc.xy + vec2(0.0, -d.y)), 0.0, 1.0);
	
	lefta_coord = clamp(vec2(texc.xy + vec2(-d.x, d.y)), 0.0, 1.0);
	righta_coord = clamp(vec2(texc.xy + vec2(d.x, d.y)), 0.0, 1.0);
	leftb_coord = clamp(vec2(texc.xy + vec2(-d.x, -d.y)), 0.0, 1.0);
	rightb_coord = clamp(vec2(texc.xy + vec2(d.x, -d.y)), 0.0, 1.0);
}
'''


class IsfMotionDataMosh(GlslEffect):
    name = 'isf_motion_data_mosh'
    label = 'Motion Data Mosh'
    description = 'Optical-flow driven data mosh using persistent motion and frame buffers'
    dialect = 'isf'
    GLSL = GLSL
    VS = VS
    stage = 'glitch'
    scale = 0.35
    events = ('takeKeyframe', 'resetNow')
    params = effect_params({
        "mosh_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.85,
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
        "mosh_hold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.92,
            "x-ui": {
                "label": "Frame Hold",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "moshHold"
            }
        },
        "fresh_mix": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.03,
            "x-ui": {
                "label": "Recovery",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "freshMix"
            }
        },
        "flow_scale": {
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
                "u": "flowScale"
            }
        },
        "flow_persistence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.88,
            "x-ui": {
                "label": "Flow Persistence",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flowPersistence"
            }
        },
        "flow_offset": {
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
                "u": "flowOffset"
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
        "block_jitter": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.16,
            "x-ui": {
                "label": "Block Jitter",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "blockJitter"
            }
        },
        "chroma_slip": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.22,
            "x-ui": {
                "label": "RGB Slip",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "chromaSlip"
            }
        },
        "edge_injection": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.18,
            "x-ui": {
                "label": "Edge Inject",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "edgeInjection"
            }
        },
        "keyframe_mix": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Keyframe Mix",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "keyframeMix"
            }
        },
        "update_keyframe": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Update Keyframe"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "updateKeyframe"
            }
        },
        "invert_motion": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Invert Motion"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "invertMotion"
            }
        },
        "display_mode": {
            "type": "integer",
            "enum": [
                0,
                1,
                2,
                3
            ],
            "default": 0,
            "x-ui": {
                "label": "Display",
                "widget": "select",
                "labels": [
                    "Mosh",
                    "Flow",
                    "Motion Mask",
                    "Keyframe"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "displayMode"
            }
        }
    })
