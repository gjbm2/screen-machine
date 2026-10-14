"""V002 Glitch Analog: Emulates classic analog video interference, distortion and sync issues.

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/v002%20Glitch%20Analog.fs
Author: Vade / VIDVOX
License: CC-BY-NC-SA-3.0
license_class: noncommercial
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
v002 port: v002 sources are CC-BY-NC-SA-3.0.
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*
{
  "CATEGORIES" : [
    "Glitch", "Distortion Effect", "v002"
  ],
  "DESCRIPTION" : "Emulates classic analog video interference, distortion and sync issues.",
  "ISFVSN" : "2",
  "INPUTS" : [
    {
      "NAME" : "inputImage",
      "TYPE" : "image"
    },
    {
      "NAME" : "inputDistortionImage",
      "TYPE" : "image"
    },
    {
      "NAME" : "inputDistortion",
      "TYPE" : "float",
      "MAX" : 5,
      "DEFAULT" : 0.0,
      "LABEL" : "Distortion",
      "MIN" : 0
    },
    {
      "NAME" : "inputBarsAmount",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0.25,
      "LABEL" : "Distortion Mix",
      "MIN" : 0
    },
    {
      "NAME" : "inputVSYNC",
      "TYPE" : "float",
      "MAX" : 2,
      "DEFAULT" : 0.0,
      "LABEL" : "V Sync",
      "MIN" : 0
    },
    {
      "NAME" : "inputHSYNC",
      "TYPE" : "float",
      "MAX" : 2,
      "DEFAULT" : 0.0,
      "LABEL" : "H Sync",
      "MIN" : 0
    },
    {
      "NAME" : "inputResolution",
      "TYPE" : "float",
      "MAX" : 10,
      "DEFAULT" : 5,
      "LABEL" : "Scan Line Size",
      "MIN" : 1
    },
    {
      "NAME" : "inputResolutionMix",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0.5,
      "LABEL" : "Scan Lin Mix",
      "MIN" : 0
    }
  ],
  "CREDIT" : "Vade / VIDVOX"
}
*/



//	Original v002 Analog Glitch by Vade
//	https://github.com/v002/v002-Glitch/blob/master/v002AnalogGlitchPlugIn.m

//	Adapted by VIDVOX 2025


void main (void) 	{ 		
	vec2 point = isf_FragNormCoord.xy;
	
    // sample center of the 1st pixel, down the height
	vec4 bars = IMG_NORM_PIXEL(inputDistortionImage, vec2(0.5, point.y));

	// scanlines
	float stripe = mod(floor(gl_FragCoord.y), floor(inputResolution)) / floor(inputResolution);
	float scanlineMask = mix(1.5, 0.75, stripe);
	scanlineMask = mix(1.0, scanlineMask, inputResolutionMix);
	
	// get rough luma 
	vec4 key = IMG_PIXEL(inputImage, (vec2(point.y, point.y)) * RENDERSIZE);
	key += IMG_PIXEL(inputImage, (1.0 - vec2(point.y, point.y)) * RENDERSIZE);
	key -= bars.r;
	float d = (key.r + key.g + key.b) / 3.0 - 0.5;
	point.x -= d * inputDistortion * 0.1;

	//sync			
	vec2 texcoord = point + ( mod(vec2(inputHSYNC, inputVSYNC), 1.0)); 
	
	// wrap
	texcoord = mod(texcoord, 1.0);
	
	// outout
	vec4 result = IMG_PIXEL(inputImage, texcoord * RENDERSIZE);
	result.rgb = result.rgb * scanlineMask;
    
    gl_FragColor = mix(result, bars*result, inputBarsAmount);
}

'''


class IsfV002GlitchAnalog(GlslEffect):
    name = 'isf_v002_glitch_analog'
    label = 'V002 Glitch Analog'
    description = 'Emulates classic analog video interference, distortion and sync issues.'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.6
    params = effect_params({
        "input_distortion": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 5.0,
            "default": 0.0,
            "x-ui": {
                "label": "Distortion",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "inputDistortion"
            }
        },
        "input_bars_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.25,
            "x-ui": {
                "label": "Distortion Mix",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "inputBarsAmount"
            }
        },
        "input_vsync": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.0,
            "x-ui": {
                "label": "V Sync",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "inputVSYNC"
            }
        },
        "input_hsync": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.0,
            "x-ui": {
                "label": "H Sync",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "inputHSYNC"
            }
        },
        "input_resolution": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 10.0,
            "default": 5.0,
            "x-ui": {
                "label": "Scan Line Size",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "inputResolution"
            }
        },
        "input_resolution_mix": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Scan Lin Mix",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "inputResolutionMix"
            }
        }
    })
