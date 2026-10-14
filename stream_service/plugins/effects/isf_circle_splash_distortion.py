"""Circle Splash Distortion: Circle Splash Distortion (Distortion Effect).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Circle%20Splash%20Distortion.fs
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
  "ISFVSN" : "2",
  "INPUTS" : [
    {
      "NAME" : "inputImage",
      "TYPE" : "image"
    },
    {
      "NAME" : "radius",
      "TYPE" : "float",
      "MAX" : 0.75,
      "DEFAULT" : 0.125,
      "MIN" : 0
    },
    {
      "NAME" : "streaks",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0,
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
  "CREDIT" : "by VIDVOX"
}
*/




//	Pretty simple ‚Äì¬ if we're inside the radius, draw as normal
//	If we're outside the circle grab the last color along the angle

#ifndef GL_ES
float distance (vec2 inCenter, vec2 pt)
{
	float tmp = pow(inCenter.x-pt.x,2.0)+pow(inCenter.y-pt.y,2.0);
	return pow(tmp,0.5);
}
#endif

void main() {
	vec2 uv = vec2(isf_FragNormCoord[0],isf_FragNormCoord[1]);
	vec2 texSize = RENDERSIZE;
	vec2 tc = uv * texSize;
	vec2 tc2 = uv * texSize;
	vec2 modifiedCenter = center * texSize;
	float r = distance(modifiedCenter, tc);
	float render_length = length(RENDERSIZE);
	float a = atan ((tc.y-modifiedCenter.y),(tc.x-modifiedCenter.x));
	float radius_sized = clamp(radius * render_length, 1.0, render_length);
	
	tc -= modifiedCenter;
	tc2 -= modifiedCenter;

	if (r < radius_sized) 	{
		tc.x = r * cos(a);
		tc.y = r * sin(a);
		tc2 = tc;
	}
	else	{
		tc.x = radius_sized * cos(a);
		tc.y = radius_sized * sin(a);
		tc2.x = (radius_sized + streaks * render_length) * cos(a);
		tc2.y = (radius_sized + streaks * render_length) * sin(a); 
	}
	tc += modifiedCenter;
	tc2 += modifiedCenter;
	vec2 loc = tc / texSize;

	if ((loc.x < 0.0)||(loc.y < 0.0)||(loc.x > 1.0)||(loc.y > 1.0))	{
		gl_FragColor = vec4(0.0);
	}
	else	{
		vec4 result = IMG_NORM_PIXEL(inputImage, loc);
		if (streaks > 0.0)	{
			vec2 loc2 = tc2 / texSize;
			vec4 mixColor = IMG_NORM_PIXEL(inputImage, loc2);
			result = mix(result, mixColor, clamp(2.0*((r - radius_sized)/(render_length))*streaks,0.0,1.0));
		}
		gl_FragColor = result;
	}
}'''


class IsfCircleSplashDistortion(GlslEffect):
    name = 'isf_circle_splash_distortion'
    label = 'Circle Splash Distortion'
    description = 'Circle Splash Distortion (Distortion Effect).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    params = effect_params({
        "radius": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.75,
            "default": 0.125,
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
        "streaks": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Streaks",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "streaks"
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
