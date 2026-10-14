"""Monochrome Channelmix: Creates a BW image from RGB

Source: https://github.com/icalvin102/ISF/blob/HEAD/MonochromeChannelmix.fs
Author: icalvin102 (calvin@icalvin.de)
License: MIT
license_class: permissive
Imported verbatim from GLSL (icalvin102/ISF); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository icalvin102/ISF LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*
{
  "CATEGORIES" : [
    "icalvin102"
  ],
  "DESCRIPTION" : "Creates a BW image from RGB",
  "ISFVSN" : "2",
  "INPUTS" : [
    {
      "NAME" : "inputImage",
      "TYPE" : "image"
    },
    {
      "NAME" : "factor",
      "TYPE" : "color",
      "DEFAULT" : [
        0.5,
        0.5,
        0.5,
        1
      ],
      "LABEL" : "Mix Factor"
    }
  ],
  "PASSES" : [
    {
      "DESCRIPTION" : "Renderpass 0"
    }
  ],
  "CREDIT" : "icalvin102 (calvin@icalvin.de)"
}
*/

float channelmix(vec3 c, vec3 f){
	c *= f;
	return c.r+c.g+c.b;
}

void main()	{
	vec4		inputPixelColor;
	inputPixelColor = IMG_THIS_PIXEL(inputImage);
	float c = channelmix(inputPixelColor.rgb, factor.rgb);

	gl_FragColor = vec4(vec3(c), inputPixelColor.a);
}
'''


class IsfMonochromeChannelmix(GlslEffect):
    name = 'isf_monochrome_channelmix'
    label = 'Monochrome Channelmix'
    description = 'Creates a BW image from RGB'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'colour'
    params = effect_params({
        "factor": {
            "type": "string",
            "format": "color",
            "default": "#808080",
            "x-ui": {
                "label": "Mix Factor",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "factor",
                "color": 4,
                "alpha": 1.0
            }
        }
    })
