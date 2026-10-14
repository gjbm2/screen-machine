"""Color History: Displays Data Over Time

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Color%20History.fs
Author: VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*
{
  "CATEGORIES" : [
    "Color"
  ],
  "DESCRIPTION" : "Displays Data Over Time",
  "ISFVSN" : "2",
  "INPUTS" : [
    {
      "LABELS" : [
        "Color",
        "Lines"
      ],
      "NAME" : "displayMode",
      "TYPE" : "long",
      "DEFAULT" : 0,
      "VALUES" : [
        0,
        1
      ]
    },
    {
      "NAME" : "data",
      "TYPE" : "color",
      "DEFAULT" : [
        0.95,
        0.25,
        0,
        1
      ]
    }
  ],
  "PASSES" : [
    {
      "PERSISTENT" : true,
      "WIDTH" : "$WIDTH",
      "HEIGHT" : "1",
      "TARGET" : "dataHistory",
      "FLOAT" : true
    },
    {

    }
  ],
  "CREDIT" : "VIDVOX"
}
*/

void main()	{
	vec4		inputPixelColor = vec4(0.0);
	if (PASSINDEX == 0)	{
		vec2	loc = gl_FragCoord.xy;
		if (floor(loc.x) == 0.0)	{
			inputPixelColor = data;
		}
		else	{
			loc.x = loc.x - 1.0;
			inputPixelColor = IMG_PIXEL(dataHistory,loc);
		}
	}
	else	{
		vec2	loc = gl_FragCoord.xy;
		vec4	val = IMG_PIXEL(dataHistory,loc);
		if (displayMode == 0)	{
			inputPixelColor = val;
		}
		else if (displayMode == 1)	{
			float	tmp = floor(val.r * RENDERSIZE.y);
			inputPixelColor.a = val.a;
			if (floor(loc.y) == tmp)	{
				inputPixelColor.r = 1.0;
				inputPixelColor.a = 1.0;
			}
			tmp = floor(val.g * RENDERSIZE.y);
			if (floor(loc.y) == tmp)	{
				inputPixelColor.g = 1.0;
				inputPixelColor.a = 1.0;
			}
			tmp = floor(val.b * RENDERSIZE.y);
			if (floor(loc.y) == tmp)	{
				inputPixelColor.b = 1.0;
				inputPixelColor.a = 1.0;
			}
		}
		
	}
	gl_FragColor = inputPixelColor;
}
'''


class IsfColorHistory(GlslSource):
    name = 'isf_color_history'
    label = 'Color History'
    description = 'Displays Data Over Time'
    dialect = 'isf'
    GLSL = GLSL
    params = scene_params({
        "display_mode": {
            "type": "integer",
            "enum": [
                0,
                1
            ],
            "default": 0,
            "x-ui": {
                "label": "Display Mode",
                "widget": "select",
                "labels": [
                    "Color",
                    "Lines"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "displayMode"
            }
        },
        "data": {
            "type": "string",
            "format": "color",
            "default": "#f24000",
            "x-ui": {
                "label": "Data",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "data",
                "color": 4,
                "alpha": 1.0
            }
        }
    }, render_scale=0.5)
