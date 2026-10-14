"""Replicate: Replicate (Tile Effect).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Replicate.fs
Author: VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Tile Effect"
    ],
    "CREDIT": "",
    "DESCRIPTION": "",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0.5,
            "IDENTITY": 1,
            "MAX": 2,
            "MIN": 0,
            "NAME": "startSize",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1,
            "MAX": 1,
            "MIN": 0,
            "NAME": "startOpacity",
            "TYPE": "float"
        },
        {
            "NAME": "startCenter",
            "TYPE": "point2D"
        },
        {
            "DEFAULT": 0.25,
            "MAX": 1,
            "MIN": 0,
            "NAME": "startPadding",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.25,
            "MAX": 2,
            "MIN": 0,
            "NAME": "endSize",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1,
            "MAX": 1,
            "MIN": 0,
            "NAME": "endOpacity",
            "TYPE": "float"
        },
        {
            "NAME": "endCenter",
            "TYPE": "point2D"
        },
        {
            "DEFAULT": 0.1,
            "MAX": 1,
            "MIN": 0,
            "NAME": "endPadding",
            "TYPE": "float"
        },
        {
            "DEFAULT": 5,
            "MAX": 15,
            "MIN": 1,
            "NAME": "repetitions",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2"
}
*/


vec2 paddedZoomedPosition(vec2 loc, float zl, vec2 c, float p)	{
	vec2		returnMe = loc;
	float		zoomMult = (1.0/zl);
	vec2		modifiedCenter = 2.0*(1.0+p)*c/RENDERSIZE-(1.0+p);
	float		modifiedPadding = p;
	
	returnMe.x = (returnMe.x)*zoomMult + p/2.0 - modifiedCenter.x;
	returnMe.y = (returnMe.y)*zoomMult + p/2.0 - modifiedCenter.y;
	returnMe.x = mod(returnMe.x,1.0+modifiedPadding) - p/2.0;
	returnMe.y = mod(returnMe.y,1.0+modifiedPadding) - p/2.0;
	
	return returnMe;
}


void main()	{
	vec4		inputPixelColor = vec4(0.0);

	int			depth = int(repetitions);
	vec2		loc = isf_FragNormCoord;
	float		minZoomLevel = (1.0/RENDERSIZE.x);
	float		startZoomLevel = (startSize < minZoomLevel) ?  minZoomLevel : startSize;
	float		endZoomLevel = (endSize < minZoomLevel) ?  minZoomLevel : endSize;
	float		zoomIncrement = (depth < 2) ? 0.0 : (endZoomLevel - startZoomLevel)/float(depth-1);
	vec2		centerIncrement = (depth < 2) ? vec2(0.0) : (endCenter - startCenter)/float(depth-1);
	float		paddingIncrement = (depth < 2) ? 0.0 : (endPadding - startPadding)/float(depth-1);
	float		opacityIncrement = (depth < 2) ? 0.0 : (endOpacity - startOpacity)/float(depth-1);
	
	for (int i = 0;i < 15;++i)	{
		if (i >= depth)
			break;
		float	modZoom = startZoomLevel + zoomIncrement * float(i);
		vec2	modCenter = startCenter + centerIncrement * float(i);
		float	modPad = startPadding + paddingIncrement * float(i);
		float	modOpacity = startOpacity + opacityIncrement * float(i);
		modOpacity = clamp(modOpacity,0.0,1.0);
		loc = paddedZoomedPosition(isf_FragNormCoord,modZoom,modCenter,modPad);
		if ((loc.x < 0.0)||(loc.y < 0.0)||(loc.x > 1.0)||(loc.y > 1.0))	{
			//inputPixelColor = vec4(0.0);
		}
		else	{
			vec4	tmpColor = IMG_NORM_PIXEL(inputImage,loc);
			inputPixelColor.rgb = inputPixelColor.rgb + tmpColor.rgb * tmpColor.a * modOpacity;
			inputPixelColor.a += (tmpColor.a * modOpacity);
			if (inputPixelColor.a > 0.99)	{
				break;
			}
		}
	}
	
	gl_FragColor = inputPixelColor;
}
'''


class IsfReplicate(GlslEffect):
    name = 'isf_replicate'
    label = 'Replicate'
    description = 'Replicate (Tile Effect).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    params = effect_params({
        "start_size": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.5,
            "x-ui": {
                "label": "Start Size",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "startSize"
            }
        },
        "start_opacity": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "Start Opacity",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "startOpacity"
            }
        },
        "start_center_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Start Center x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "startCenter",
                "c": 0
            }
        },
        "start_center_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Start Center y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "startCenter",
                "c": 1
            }
        },
        "start_padding": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.25,
            "x-ui": {
                "label": "Start Padding",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "startPadding"
            }
        },
        "end_size": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.25,
            "x-ui": {
                "label": "End Size",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "endSize"
            }
        },
        "end_opacity": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "End Opacity",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "endOpacity"
            }
        },
        "end_center_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "End Center x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "endCenter",
                "c": 0
            }
        },
        "end_center_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "End Center y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "endCenter",
                "c": 1
            }
        },
        "end_padding": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.1,
            "x-ui": {
                "label": "End Padding",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "endPadding"
            }
        },
        "repetitions": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 15.0,
            "default": 5.0,
            "x-ui": {
                "label": "Repetitions",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "repetitions"
            }
        }
    })
