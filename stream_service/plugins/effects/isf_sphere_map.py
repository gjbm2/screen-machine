"""Sphere Map: Maps video onto a sphere

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Sphere%20Map.fs
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
    "DESCRIPTION": "Maps video onto a sphere",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0.5,
            "LABEL": "Image Scale",
            "MAX": 1,
            "MIN": 0.125,
            "NAME": "imageScale",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1,
            "LABEL": "Radius Scale",
            "MAX": 1.999,
            "MIN": 0,
            "NAME": "radiusScale",
            "TYPE": "float"
        },
        {
            "DEFAULT": [
                0,
                0
            ],
            "LABEL": "Rotate",
            "MAX": [
                1,
                1
            ],
            "MIN": [
                0,
                0
            ],
            "NAME": "pointInput",
            "TYPE": "point2D"
        }
    ],
    "ISFVSN": "2"
}
*/



const float pi = 3.14159265359;


void main()	{
	vec4		inputPixelColor = vec4(0.0);
	vec2		rotate = pointInput;
 	vec2 		p = 2.0 * isf_FragNormCoord.xy - 1.0;
 	float		aspect = RENDERSIZE.x / RENDERSIZE.y;
 	p.x = p.x * aspect;
 	
 	float		r = sqrt(dot(p,p)) * (2.0-radiusScale);
 	if (r < 1.0)	{
		vec2 uv;
    	float f = imageScale * (1.0-sqrt(1.0-r))/(r);
    	uv.x = mod(p.x*f + rotate.x,1.0);
    	uv.y = mod(p.y*f + rotate.y,1.0);
    	inputPixelColor = IMG_NORM_PIXEL(inputImage, uv);
	}


	//	both of these are also the same
	//inputPixelColor = IMG_NORM_PIXEL(inputImage, loc);
	
	gl_FragColor = inputPixelColor;
}
'''


class IsfSphereMap(GlslEffect):
    name = 'isf_sphere_map'
    label = 'Sphere Map'
    description = 'Maps video onto a sphere'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    params = effect_params({
        "image_scale": {
            "type": "number",
            "minimum": 0.125,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Image Scale",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "imageScale"
            }
        },
        "radius_scale": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.999,
            "default": 1.0,
            "x-ui": {
                "label": "Radius Scale",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "radiusScale"
            }
        },
        "point_input_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Rotate x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "pointInput",
                "c": 0
            }
        },
        "point_input_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Rotate y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "pointInput",
                "c": 1
            }
        }
    })
