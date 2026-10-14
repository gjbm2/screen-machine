"""Circle Warp: Warps an image to fit in a circle by fitting the height of the image to the height of a circle

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Circle%20Warp.fs
Author: VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Distortion Effect"
    ],
    "CREDIT": "VIDVOX",
    "DESCRIPTION": "Warps an image to fit in a circle by fitting the height of the image to the height of a circle",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0.5,
            "MAX": 0.5,
            "MIN": 0,
            "NAME": "radius",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1,
            "MAX": 2,
            "MIN": 0,
            "NAME": "width",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "MAX": 1,
            "MIN": 0,
            "NAME": "resultRotation",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2"
}
*/


const float pi = 3.1415926535897932384626433832795;

vec2 rotatePointNorm(vec2 pt, float rot)	{
	vec2 returnMe = pt;

	float r = distance(vec2(0.50), returnMe);
	float a = atan((returnMe.y-0.5),(returnMe.x-0.5));

	returnMe.x = r * cos(a + 2.0 * pi * rot - pi) + 0.5;
	returnMe.y = r * sin(a + 2.0 * pi * rot - pi) + 0.5;
	
	returnMe = returnMe;
	
	return returnMe;
}

void main()	{
	vec4		inputPixelColor;
	vec2		pt = isf_FragNormCoord;
	vec2		ct = vec2(0.5,0.5);
	
	pt -= ct;
	pt.x /= width;
	pt += ct;
	
	pt = mix(vec2((pt.x*RENDERSIZE.x/RENDERSIZE.y)-(RENDERSIZE.x*.5-RENDERSIZE.y*.5)/RENDERSIZE.y,pt.y), 
				vec2(pt.x,pt.y*(RENDERSIZE.y/RENDERSIZE.x)-(RENDERSIZE.y*.5-RENDERSIZE.x*.5)/RENDERSIZE.x), 
				step(RENDERSIZE.x,RENDERSIZE.y));
	pt = rotatePointNorm(pt,resultRotation+0.5);
	if (distance(pt,ct) < radius)	{
		float		a = (atan(ct.y-pt.y,ct.x-pt.x) + pi) / (2.0*pi);
		//inputPixelColor = IMG_NORM_PIXEL(inputImage,pt);
		pt.y -= 0.5;
		pt.y /= 2.0*sqrt(pow(radius,2.0)-pow((pt.x-0.5),2.0));
		pt.y += 0.5;
		inputPixelColor = IMG_NORM_PIXEL(inputImage,pt);
	}
	
	gl_FragColor = inputPixelColor;
}
'''


class IsfCircleWarp(GlslEffect):
    name = 'isf_circle_warp'
    label = 'Circle Warp'
    description = 'Warps an image to fit in a circle by fitting the height of the image to the height of a circle'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    params = effect_params({
        "radius": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.5,
            "default": 0.5,
            "x-ui": {
                "label": "Radius",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "radius"
            }
        },
        "width": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 1.0,
            "x-ui": {
                "label": "Width",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "width"
            }
        },
        "result_rotation": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Result Rotation",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "resultRotation"
            }
        }
    })
