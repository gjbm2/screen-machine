"""Circular Feedback Mask: Circular Feedback Mask (Feedback).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Circular%20Feedback%20Mask.fs
Author: VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*
{
  "CATEGORIES" : [
    "Feedback"
  ],
  "DESCRIPTION" : "",
  "ISFVSN" : "2",
  "INPUTS" : [
    {
      "NAME" : "inputImage",
      "TYPE" : "image"
    },
    {
      "NAME" : "maskRadius",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0.25,
      "MIN" : 0
    },
    {
      "NAME" : "feedbackRate",
      "TYPE" : "float",
      "MAX" : 16,
      "DEFAULT" : 1,
      "MIN" : 0
    },
    {
      "NAME" : "twirlAmount",
      "TYPE" : "float",
      "MAX" : 0.25,
      "DEFAULT" : 0,
      "MIN" : -0.25
    },
    {
      "NAME" : "fadeRate",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0,
      "MIN" : 0
    },
    {
      "NAME" : "centerFeedback",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0,
      "MIN" : 0
    },
    {
      "NAME" : "feedbackCenter",
      "TYPE" : "point2D",
      "MAX" : [
        1,
        1
      ],
      "DEFAULT" : [
        0.5,
        0.5
      ],
      "MIN" : [
        0,
        0
      ]
    },
    {
      "LABELS" : [
        "Mask",
        "CenteredMask",
        "Scaled",
        "Wrap",
        "MirrorWrap",
        "InvertedMask"
      ],
      "NAME" : "styleMode",
      "TYPE" : "long",
      "DEFAULT" : 2,
      "VALUES" : [
        0,
        1,
        2,
        3,
        4,
        5
      ]
    },
    {
      "NAME" : "clearBuffer",
      "TYPE" : "event"
    }
  ],
  "PASSES" : [
    {
      "TARGET" : "feedbackBuffer",
      "PERSISTENT" : true
    }
  ],
  "CREDIT" : "VIDVOX"
}
*/

const float pi = 3.1415926535897932384626433832795;
const float tau =  6.2831853071795864769252867665590;


void main()	{
	vec4		inputPixelColor = vec4(0.0);
	vec2		loc = gl_FragCoord.xy;
	vec2		locCenter = feedbackCenter * RENDERSIZE;
	float		scaledRadius = maskRadius * min(RENDERSIZE.x,RENDERSIZE.y);
	float		dist = distance(locCenter,loc);
	bool		invertMask = (styleMode == 5);
	//	if within the shape, just do the shape
	if (((dist>scaledRadius)&&(invertMask))||((dist<scaledRadius)&&(invertMask==false)))	{
		if (styleMode == 1)	{
			loc -= (locCenter-RENDERSIZE/2.0);
		}
		else if (styleMode == 2)	{
			loc -= (locCenter-RENDERSIZE/2.0);
			loc -= RENDERSIZE/2.0;
			loc /= (2.0 * maskRadius);
			loc += RENDERSIZE/2.0;
		}
		else if (styleMode == 3)	{
			float	r = distance(locCenter,loc);
			float 	a = atan((loc.y-locCenter.y),(loc.x-locCenter.x));	
			a = (a + pi)/(tau);
			a = mod(a+0.75,1.0);
			r /= scaledRadius;
			loc = RENDERSIZE * vec2(a,r);
		}
		else if (styleMode == 4)	{
			float	r = distance(locCenter,loc);
			float 	a = atan((loc.y-locCenter.y),(loc.x-locCenter.x));	
			a = (a + pi)/(tau);
			a = mod(a+0.75,1.0);
			a = (a < 0.5) ? a * 2.0 : 2.0 - a * 2.0;
			r /= scaledRadius;
			loc = RENDERSIZE * vec2(a,r);
		}
		inputPixelColor = IMG_PIXEL(inputImage,loc);
		if ((centerFeedback > 0.0)&&(clearBuffer == false))	{
			inputPixelColor = mix(inputPixelColor,IMG_THIS_PIXEL(feedbackBuffer),centerFeedback);	
		}
		//inputPixelColor = vec4(loc.x,loc.y,0.0,1.0);
	}
	else if (clearBuffer == false)	{
		//float	r = distance(RENDERSIZE/2.0,loc);
		float 	a = atan((loc.y-locCenter.y),(loc.x-locCenter.x));
		float	shiftAmount = -1.0 * feedbackRate;
		vec2	shift = shiftAmount * vec2(cos(a + twirlAmount * tau), sin(a + twirlAmount * tau));
		loc = (invertMask) ? loc - shift : loc + shift;
		inputPixelColor = IMG_PIXEL(feedbackBuffer,loc);
		inputPixelColor.a -= fadeRate / 50.0;
		//inputPixelColor = vec4((a+pi)/(2.0*pi),2.0*r/RENDERSIZE.x,0.0,1.0);
		//inputPixelColor = vec4(shift.x*2.0-1.0,shift.y*2.0-1.0,0.0,1.0);
	}
	
	gl_FragColor = inputPixelColor;
}
'''


class IsfCircularFeedbackMask(GlslEffect):
    name = 'isf_circular_feedback_mask'
    label = 'Circular Feedback Mask'
    description = 'Circular Feedback Mask (Feedback).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'feedback'
    scale = 0.95
    events = ('clearBuffer',)
    params = effect_params({
        "mask_radius": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.25,
            "x-ui": {
                "label": "Mask Radius",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "maskRadius"
            }
        },
        "feedback_rate": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 16.0,
            "default": 1.0,
            "x-ui": {
                "label": "Feedback Rate",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "feedbackRate"
            }
        },
        "twirl_amount": {
            "type": "number",
            "minimum": -0.25,
            "maximum": 0.25,
            "default": 0.0,
            "x-ui": {
                "label": "Twirl Amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "twirlAmount"
            }
        },
        "fade_rate": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Fade Rate",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "fadeRate"
            }
        },
        "center_feedback": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Center Feedback",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "centerFeedback"
            }
        },
        "feedback_center_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Feedback Center x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "feedbackCenter",
                "c": 0
            }
        },
        "feedback_center_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Feedback Center y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "feedbackCenter",
                "c": 1
            }
        },
        "style_mode": {
            "type": "integer",
            "enum": [
                0,
                1,
                2,
                3,
                4,
                5
            ],
            "default": 2,
            "x-ui": {
                "label": "Style Mode",
                "widget": "select",
                "labels": [
                    "Mask",
                    "CenteredMask",
                    "Scaled",
                    "Wrap",
                    "MirrorWrap",
                    "InvertedMask"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "styleMode"
            }
        }
    })
