"""Long Exposure: Bright objects burn in and optionally decay

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Long%20Exposure.fs
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
    "Color Effect", "Feedback", "Film"
  ],
  "DESCRIPTION" : "Bright objects burn in and optionally decay",
  "ISFVSN" : "2",
  "INPUTS" : [
    {
      "NAME" : "inputImage",
      "TYPE" : "image"
    },
    {
      "NAME" : "absorptionRate",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0.5,
      "MIN" : -1,
      "LABEL" : "Absorption Rate"
    },
    {
      "NAME" : "dischargeRate",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0.25,
      "MIN" : 0,
      "LABEL" : "Discharge Rate"
    }
  ],
  "PASSES" : [
    {
      "TARGET" : "feedbackBuffer",
      "PERSISTENT" : true,
      "FLOAT" : true
    },
    {

    }
  ],
  "CREDIT" : "VIDVOX"
}
*/

void main()
{
	vec4		freshPixel = IMG_PIXEL(inputImage,gl_FragCoord.xy);
	vec4		stalePixel = IMG_PIXEL(feedbackBuffer,gl_FragCoord.xy);
	vec4		resultPixel = vec4(0.0);
	
	//	absorb and discharge
	if (PASSINDEX==0)	{
		//	start with the old pixel amount
		resultPixel = stalePixel;
		//	discharge from previous pass
		resultPixel *= (1.0 - dischargeRate);
		//	add this pass
		resultPixel += (freshPixel * absorptionRate);
		resultPixel = clamp(resultPixel,0.0,1.0);
	}
	//	composite 
	else if (PASSINDEX==1)	{
		resultPixel = freshPixel + stalePixel;
	}
	
	gl_FragColor = resultPixel;
}
'''


class IsfLongExposure(GlslEffect):
    name = 'isf_long_exposure'
    label = 'Long Exposure'
    description = 'Bright objects burn in and optionally decay'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'feedback'
    scale = 0.75
    params = effect_params({
        "absorption_rate": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Absorption Rate",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "absorptionRate"
            }
        },
        "discharge_rate": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.25,
            "x-ui": {
                "label": "Discharge Rate",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "dischargeRate"
            }
        }
    })
