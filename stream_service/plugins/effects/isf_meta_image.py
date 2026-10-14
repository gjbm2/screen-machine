"""Meta Image: Meta Image (Stylize, Tile Effect).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Meta%20Image.fs
Author: by Toneburst
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"CREDIT": "by Toneburst",
	"ISFVSN": "2",
	"CATEGORIES": [
		"Stylize", "Tile Effect"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "cell_size",
			"TYPE": "float",
			"MIN": 0.001,
			"MAX": 1.0,
			"DEFAULT": 0.125
		},
		{
			"NAME": "zoom_tile",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 2.0,
			"DEFAULT": 1.0
		},
		{
			"NAME": "mixAmt",
			"LABEL": "mix",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.5
		},
		{
			"NAME": "mode",
			"VALUES": [
				0,
				1
			],
			"LABELS": [
				"Multiply",
				"Mix"
			],
			"DEFAULT": 0,
			"TYPE": "long"
		}
	]
}*/

void main()
{
// CALCULATE EDGES OF CURRENT CELL
	// Position of current pixel
	vec2 xy = gl_FragCoord.xy / RENDERSIZE.xy;
	// Left and right of tile
	float CellWidth = cell_size;
	float CellHeight = cell_size;
	float x1 = floor(xy.x / CellWidth)*CellWidth;
	float x2 = clamp((ceil(xy.x / CellWidth)*CellWidth), 0.0, 1.0);
	// Top and bottom of tile
	float y1 = floor(xy.y / CellHeight)*CellHeight;
	float y2 = clamp((ceil(xy.y / CellHeight)*CellHeight), 0.0, 1.0);

	// GET AVERAGE CELL COLOUR
	// Average left and right pixels
	vec4 avgX = (IMG_NORM_PIXEL(inputImage, vec2(x1, y1))+(IMG_NORM_PIXEL(inputImage, vec2(x2, y1)))) / 2.0;
	// Average top and bottom pixels
	vec4 avgY = (IMG_NORM_PIXEL(inputImage, vec2(x1, y1))+(IMG_NORM_PIXEL(inputImage, vec2(x1, y2)))) / 2.0;
	// Centre pixel
	vec4 avgC = IMG_NORM_PIXEL(inputImage, vec2(x1+(CellWidth/2.0), y2+(CellHeight/2.0)));	// Average the averages + centre
	vec4 avgClr = (avgX+avgY+avgC) / 3.0;

	// GET PIXELS FROM LITTLE IMAGE	
	// X-position in current cell
	float cellPosX = (xy.x - x1) / CellWidth;
	// Y-position in current cell
	float cellPosY = (xy.y - y1) / CellHeight;
	
	vec2 loc = vec2(cellPosX, cellPosY);
	vec2 modifiedCenter = vec2(0.5);
	loc.x = (loc.x - modifiedCenter.x)*(1.0/zoom_tile) + modifiedCenter.x;
	loc.y = (loc.y - modifiedCenter.y)*(1.0/zoom_tile) + modifiedCenter.y;
	
	vec4 littlePix;
	if ((loc.x < 0.0)||(loc.y < 0.0)||(loc.x > 1.0)||(loc.y > 1.0))	{
		littlePix = vec4(0.0);
	}
	else	{
		littlePix = IMG_NORM_PIXEL(inputImage, loc);
	}
	
	// MULTIPLY LITTLE IMAGE COLOUR WITH AVERAGE CELL COLOUR AND OUTPUT
	if (mode == 0)	{
		gl_FragColor = vec4(littlePix * avgClr);
	}
	else	{
		gl_FragColor = vec4(mix(littlePix, avgClr, mixAmt));
	}
}
'''


class IsfMetaImage(GlslEffect):
    name = 'isf_meta_image'
    label = 'Meta Image'
    description = 'Meta Image (Stylize, Tile Effect).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    scale = 0.6
    params = effect_params({
        "cell_size": {
            "type": "number",
            "minimum": 0.001,
            "maximum": 1.0,
            "default": 0.125,
            "x-ui": {
                "label": "Cell size",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "cell_size"
            }
        },
        "zoom_tile": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 1.0,
            "x-ui": {
                "label": "Zoom tile",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "zoom_tile"
            }
        },
        "mix_amt": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "mix",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mixAmt"
            }
        },
        "mode": {
            "type": "integer",
            "enum": [
                0,
                1
            ],
            "default": 0,
            "x-ui": {
                "label": "Mode",
                "widget": "select",
                "labels": [
                    "Multiply",
                    "Mix"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "mode"
            }
        }
    })
