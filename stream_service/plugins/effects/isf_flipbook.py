"""Flipbook: Creates a flipbook like effect

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Flipbook.fs
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
    "Stylize"
  ],
  "DESCRIPTION" : "Creates a flipbook like effect",
  "ISFVSN" : "2",
  "INPUTS" : [
    {
      "NAME" : "inputImage",
      "TYPE" : "image"
    },
    {
      "NAME" : "flipRate",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0.1,
      "MIN" : 0
    },
    {
      "NAME" : "stretchMode",
      "TYPE" : "bool",
      "DEFAULT" : 1
    },
    {
      "LABELS" : [
        "Left",
        "Right",
        "Up",
        "Down"
      ],
      "NAME" : "flipDirection",
      "TYPE" : "long",
      "DEFAULT" : 3,
      "VALUES" : [
        0,
        1,
        2,
        3
      ]
    },
    {
      "NAME" : "holdTime",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0,
      "MIN" : 0
    }
  ],
  "PASSES" : [
    {
      "FLOAT" : true,
      "WIDTH" : "1",
      "HEIGHT" : "1",
      "TARGET" : "writePos",
      "PERSISTENT" : true
    },
    {
      "TARGET" : "frameGrab",
      "PERSISTENT" : true
    },
    {
      "TARGET" : "lastOutput",
      "PERSISTENT" : true
    }
  ],
  "CREDIT" : "VIDVOX"
}
*/

void main()	{
	vec4		inputPixelColor = vec4(0.0);
	
	if (PASSINDEX == 0)	{
		inputPixelColor = (FRAMEINDEX <= 1) ? vec4(0.0) : IMG_PIXEL(writePos, vec2(0.0));
		float		oldG = inputPixelColor.g;
		inputPixelColor.g = inputPixelColor.g + flipRate;
		inputPixelColor.b = 1.0;
		inputPixelColor.a = 1.0;
		if (inputPixelColor.g >= 1.0 + flipRate)	{
			//inputPixelColor.g = 0.0;
			if (inputPixelColor.r >= holdTime)	{
				inputPixelColor.b = 0.0;
				inputPixelColor.g = (holdTime > 0.0) ? 0.0 : mod(oldG,1.0);
			}
			else	{
				inputPixelColor.r = inputPixelColor.r + TIMEDELTA;
				inputPixelColor.g = oldG;
			}
		}
		else	{
			inputPixelColor.r = 0.0;
		}
	}
	else if (PASSINDEX == 1)	{
		vec4	tmpColor = (FRAMEINDEX <= 1) ? vec4(0.0) : IMG_PIXEL(writePos, vec2(0.0));
		//	update the image buffer
		if (tmpColor.b == 0.0)	{
			inputPixelColor = IMG_THIS_PIXEL(inputImage);
		}
		//
		else	{
			inputPixelColor = IMG_THIS_PIXEL(frameGrab);
		}
	}
	else if (PASSINDEX == 2)	{
		if (FRAMEINDEX <= 1)	{
			inputPixelColor = IMG_THIS_PIXEL(frameGrab);
		}
		else	{
			vec4	tmpColor = (FRAMEINDEX <= 1) ? vec4(0.0) : IMG_PIXEL(writePos, vec2(0.0));
			vec2	loc = isf_FragNormCoord;
			float	grabTime = (tmpColor.g > 1.0) ? 1.0 : tmpColor.g;
			float	transTime = 0.0;
			if (flipDirection == 0)	{
				transTime = loc.x;
				loc.x = (stretchMode) ? 1.0 - (1.0 - loc.x) / (grabTime) : loc.x - (1.0 - grabTime);
			}
			else if (flipDirection == 1)	{
				transTime = 1.0 - loc.x;
				loc.x = (stretchMode) ? loc.x / (grabTime) : loc.x + (1.0 - grabTime);
			}
			else if (flipDirection == 2)	{
				transTime = 1.0 - loc.y;
				loc.y = (stretchMode) ? loc.y / (grabTime) : loc.y + (1.0 - grabTime);
			}
			else if (flipDirection == 3)	{
				transTime = loc.y;
				loc.y = (stretchMode) ? 1.0 - (1.0 - loc.y) / (grabTime) : loc.y - (1.0 - grabTime);
			}
			
			if (transTime <= 1.0 - grabTime)	{
				inputPixelColor = IMG_THIS_PIXEL(lastOutput);
			}
			else	{
				inputPixelColor = IMG_NORM_PIXEL(frameGrab, loc);
			}
		}
	}
	
	gl_FragColor = inputPixelColor;
}
'''


class IsfFlipbook(GlslEffect):
    name = 'isf_flipbook'
    label = 'Flipbook'
    description = 'Creates a flipbook like effect'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    scale = 0.6
    params = effect_params({
        "flip_rate": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.1,
            "x-ui": {
                "label": "Flip Rate",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flipRate"
            }
        },
        "stretch_mode": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Stretch Mode"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "stretchMode"
            }
        },
        "flip_direction": {
            "type": "integer",
            "enum": [
                0,
                1,
                2,
                3
            ],
            "default": 3,
            "x-ui": {
                "label": "Flip Direction",
                "widget": "select",
                "labels": [
                    "Left",
                    "Right",
                    "Up",
                    "Down"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "flipDirection"
            }
        },
        "hold_time": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Hold Time",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "holdTime"
            }
        }
    })
