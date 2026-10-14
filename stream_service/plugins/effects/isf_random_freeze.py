"""Random Freeze: Causes only part of an image to update

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Random%20Freeze.fs
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
    "Glitch"
  ],
  "DESCRIPTION" : "Causes only part of an image to update",
  "ISFVSN" : "2",
  "INPUTS" : [
    {
      "NAME" : "inputImage",
      "TYPE" : "image"
    },
    {
      "NAME" : "maxUpdateSize",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0.5,
      "LABEL" : "Size",
      "MIN" : 0
    },
    {
      "NAME" : "maxBlendAmount",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0,
      "MIN" : 0
    },
    {
      "NAME" : "resetImage",
      "TYPE" : "event",
      "LABEL" : "Reset Image"
    }
  ],
  "PASSES" : [
    {
      "TARGET" : "lastState",
      "PERSISTENT" : true,
      "DESCRIPTION" : ""
    }
  ],
  "CREDIT" : "VIDVOX"
}
*/

float rand(vec2 co){
    return fract(sin(dot(co.xy ,vec2(12.9898,78.233))) * 43758.5453);
}

vec4 rand4(vec4 co)	{
	vec4	returnMe = vec4(0.0);
	returnMe.r = rand(co.rg);
	returnMe.g = rand(co.gb);
	returnMe.b = rand(co.ba);
	returnMe.a = rand(co.rb);
	return returnMe;
}

vec3 hsv2rgb(vec3 c)	{
	vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
	vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
	return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

const float pi = 3.14159265359;


bool pointInRect(vec2 pt, vec4 r)
{
	bool	returnMe = false;
	if ((pt.x >= r.x)&&(pt.y >= r.y)&&(pt.x <= r.x + r.z)&&(pt.y <= r.y + r.w))
		returnMe = true;
	return returnMe;
}

void main()	{
	vec2	loc = isf_FragNormCoord.xy;
	bool	doReset = ((resetImage)||(FRAMEINDEX==0));
	vec4	returnMe = (doReset) ? IMG_THIS_PIXEL(inputImage) : IMG_THIS_PIXEL(lastState);
	vec4	seeds1 = TIME * vec4(0.2123,0.34517,0.53428,0.7431);
	vec4	randCoords = rand4(seeds1);
	randCoords.zw *= maxUpdateSize;
	if (randCoords.x + randCoords.z > 1.0)
		randCoords.z = 1.0 - randCoords.x;
	if (randCoords.y + randCoords.w > 1.0)
		randCoords.w = 1.0 - randCoords.y;

	bool	isInShape = pointInRect(loc,randCoords);
	
	if (isInShape)	{
		float		mixAmount = maxBlendAmount * rand(vec2(TIME,0.32234));
		vec4		newColor = IMG_THIS_PIXEL(inputImage);
		newColor.a = 1.0;
		returnMe = mix(newColor,returnMe,mixAmount);
	}
	
	gl_FragColor = returnMe;
}
'''


class IsfRandomFreeze(GlslEffect):
    name = 'isf_random_freeze'
    label = 'Random Freeze'
    description = 'Causes only part of an image to update'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    events = ('resetImage',)
    params = effect_params({
        "max_update_size": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Size",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "maxUpdateSize"
            }
        },
        "max_blend_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Max Blend Amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "maxBlendAmount"
            }
        }
    })
