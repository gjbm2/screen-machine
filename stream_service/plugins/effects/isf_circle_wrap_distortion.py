"""Circle Wrap Distortion: Wraps the video into a circular shape

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Circle%20Wrap%20Distortion.fs
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
    "Distortion Effect"
  ],
  "DESCRIPTION" : "Wraps the video into a circular shape",
  "ISFVSN" : "2",
  "INPUTS" : [
    {
      "NAME" : "inputImage",
      "TYPE" : "image"
    },
    {
      "NAME" : "inputAngle",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0.5,
      "MIN" : 0
    },
    {
      "NAME" : "inputRadius",
      "TYPE" : "float",
      "MAX" : 2,
      "DEFAULT" : 1,
      "MIN" : 0
    },
    {
      "NAME" : "inputCenter",
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
      "NAME" : "mirror",
      "TYPE" : "bool",
      "DEFAULT" : false
    },
    {
      "NAME" : "correctAspect",
      "TYPE" : "bool",
      "DEFAULT" : true
    }
  ],
  "CREDIT" : "VIDVOX"
}
*/


const float tau = 6.28318530718;
const float pi = 3.14159265359;
const float halfpi = 1.57079632679;


void main()	{
	vec4		inputPixelColor;
	if (inputRadius > 0.0)	{
	
		vec2		loc = isf_FragNormCoord.xy;
		vec2		center = inputCenter;
	
		//	account for aspect ratio so we stay circle
		if (correctAspect)	{
			loc.x = loc.x * RENDERSIZE.x/RENDERSIZE.y;
			center.x = center.x * RENDERSIZE.x/RENDERSIZE.y;
		}
		//	translate from polar coords back to cart for this point
		//		the effect translates (x,y) to (r,theta)
		float		r = distance(loc,center);
		float		theta = (inputAngle * tau + pi + atan(loc.x-center.x,loc.y-center.y))/tau;
		
		theta = mod(theta, 1.0);
	
		//loc = vec2((r * 2.0)/inputRadius, theta / pi);
		loc = vec2(theta, (r * 2.0)/inputRadius);
		
		if ((loc.x < 0.0)||(loc.x > 1.0)||(loc.y < 0.0)||(loc.y > 1.0))
			inputPixelColor = vec4(0.0);
		else	{
			loc.y = 1.0 - loc.y;
			if (mirror)	{
				loc.x = (loc.x < 0.5) ? loc.x * 2.0 : 2.0 - loc.x * 2.0;
			}
			inputPixelColor = IMG_NORM_PIXEL(inputImage, loc);
		}
	}
	gl_FragColor = inputPixelColor;
}
'''


class IsfCircleWrapDistortion(GlslEffect):
    name = 'isf_circle_wrap_distortion'
    label = 'Circle Wrap Distortion'
    description = 'Wraps the video into a circular shape'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    params = effect_params({
        "input_angle": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Input Angle",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "inputAngle"
            }
        },
        "input_radius": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 1.0,
            "x-ui": {
                "label": "Input Radius",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "inputRadius"
            }
        },
        "input_center_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Input Center x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "inputCenter",
                "c": 0
            }
        },
        "input_center_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Input Center y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "inputCenter",
                "c": 1
            }
        },
        "mirror": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Mirror"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mirror"
            }
        },
        "correct_aspect": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Correct Aspect"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "correctAspect"
            }
        }
    })
