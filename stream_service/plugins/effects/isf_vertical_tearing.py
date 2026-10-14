"""Vertical Tearing: This introduces a vertical tearing effect similar to when GL VBL sync is off.

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Vertical%20Tearing.fs
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
    "Glitch"
  ],
  "DESCRIPTION" : "This introduces a vertical tearing effect similar to when GL VBL sync is off.",
  "ISFVSN" : "2",
  "INPUTS" : [
    {
      "NAME" : "inputImage",
      "TYPE" : "image"
    },
    {
      "NAME" : "tearPosition",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0.5,
      "MIN" : 0
    }
  ],
  "PASSES" : [
    {
      "TARGET" : "oldImage"
    },
    {
      "TARGET" : "newImage",
      "PERSISTENT" : true
    },
    {

    }
  ],
  "CREDIT" : "by VIDVOX"
}
*/

void main()
{
	//	write the previous buffer into here
	if (PASSINDEX == 0)	{
		gl_FragColor = IMG_NORM_PIXEL(newImage,isf_FragNormCoord.xy);
	}
	else if (PASSINDEX == 1)	{
		gl_FragColor = IMG_NORM_PIXEL(inputImage,isf_FragNormCoord.xy);
	}
	else if (PASSINDEX == 2)	{
		vec4		freshPixel = IMG_NORM_PIXEL(inputImage,isf_FragNormCoord.xy);
		vec4		stalePixel = IMG_NORM_PIXEL(oldImage,isf_FragNormCoord.xy);
		gl_FragColor = (isf_FragNormCoord.y > tearPosition) ? freshPixel : stalePixel;
	}
}
'''


class IsfVerticalTearing(GlslEffect):
    name = 'isf_vertical_tearing'
    label = 'Vertical Tearing'
    description = 'This introduces a vertical tearing effect similar to when GL VBL sync is off.'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.75
    params = effect_params({
        "tear_position": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Tear Position",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "tearPosition"
            }
        }
    })
