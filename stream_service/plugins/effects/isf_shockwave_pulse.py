"""Shockwave Pulse: Shockwave Pulse (Distortion Effect).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Shockwave%20Pulse.fs
Author: by VIDVOX
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
  "DESCRIPTION" : "",
  "ISFVSN" : "2",
  "INPUTS" : [
    {
      "NAME" : "inputImage",
      "TYPE" : "image"
    },
    {
      "NAME" : "pulse",
      "TYPE" : "event"
    },
    {
      "NAME" : "rate",
      "TYPE" : "float",
      "MAX" : 4,
      "DEFAULT" : 1,
      "LABEL" : "rate",
      "MIN" : 0
    },
    {
      "NAME" : "magnitude",
      "TYPE" : "float",
      "MAX" : 0.20000000000000001,
      "DEFAULT" : 0.080000000000000002,
      "LABEL" : "magnitude",
      "MIN" : 0
    },
    {
      "NAME" : "distortion",
      "TYPE" : "float",
      "MAX" : 20,
      "DEFAULT" : 10,
      "LABEL" : "distortion",
      "MIN" : 0
    },
    {
      "NAME" : "center",
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
    }
  ],
  "PASSES" : [
    {
      "PERSISTENT" : true,
      "WIDTH" : "1",
      "DESCRIPTION" : "this buffer stores the last frame's time offset in the first component of its only pixel- note that it's requesting a FLOAT target buffer...",
      "HEIGHT" : "1",
      "TARGET" : "lastTime",
      "FLOAT" : true
    },
    {

    }
  ],
  "CREDIT" : "by VIDVOX"
}
*/



void main()
{
	//	if this is the first pass, i'm going to read the position from the "lastPosition" image, and write a new position based on this and the hold variables
	if (PASSINDEX == 0)	{
		vec4		srcPixel = IMG_PIXEL(lastTime,vec2(0.5));
		//	i'm only using the X, which is the last render time we reset
		srcPixel.r = (pulse && FRAMEINDEX>10) ? 0.0 : clamp(srcPixel.r + rate * 0.01,0.0,1.0);
		gl_FragColor = srcPixel;
	}
	//	else this isn't the first pass- read the position value from the buffer which stores it
	else	{
		vec2 uv = isf_FragNormCoord.xy;
		vec2 texCoord = uv;
		vec2 mod_center = center;
		float distance = distance(uv, mod_center);
		vec4 lastPosVector = IMG_PIXEL(lastTime,vec2(0.5));
		float adustedTime = lastPosVector.r * (1.0+length(distance));

		if ( (distance <= (adustedTime + magnitude)) && (distance >= (adustedTime - magnitude)) ) 	{
			float diff = (distance - adustedTime); 
			float powDiff = 1.0 - pow(abs(diff*distortion), 
									0.8); 
			float diffTime = diff  * powDiff; 
			vec2 diffUV = normalize(uv - mod_center); 
			texCoord = uv + (diffUV * diffTime);
		} 
		gl_FragColor = IMG_NORM_PIXEL(inputImage, texCoord);
	}
}
'''


class IsfShockwavePulse(GlslEffect):
    name = 'isf_shockwave_pulse'
    label = 'Shockwave Pulse'
    description = 'Shockwave Pulse (Distortion Effect).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    scale = 0.75
    events = ('pulse',)
    params = effect_params({
        "rate": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 4.0,
            "default": 1.0,
            "x-ui": {
                "label": "rate",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "rate"
            }
        },
        "magnitude": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.2,
            "default": 0.08,
            "x-ui": {
                "label": "magnitude",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "magnitude"
            }
        },
        "distortion": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 20.0,
            "default": 10.0,
            "x-ui": {
                "label": "distortion",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "distortion"
            }
        },
        "center_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Center x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "center",
                "c": 0
            }
        },
        "center_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Center y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "center",
                "c": 1
            }
        }
    })
