"""Edge Distort: Edge Distort (Distortion Effect).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Edge%20Distort.fs
Author: by VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"CREDIT": "by VIDVOX",
	"ISFVSN": "2",
	"CATEGORIES": [
		"Distortion Effect"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "intensity",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 10.0,
			"DEFAULT": 0.2
		},
		{
			"NAME": "invert_map",
			"TYPE": "bool",
			"DEFAULT": 0.0
		}
	]
}*/

#if __VERSION__ <= 120
varying vec2 left_coord;
varying vec2 right_coord;
varying vec2 above_coord;
varying vec2 below_coord;

varying vec2 lefta_coord;
varying vec2 righta_coord;
varying vec2 leftb_coord;
varying vec2 rightb_coord;
#else
in vec2 left_coord;
in vec2 right_coord;
in vec2 above_coord;
in vec2 below_coord;

in vec2 lefta_coord;
in vec2 righta_coord;
in vec2 leftb_coord;
in vec2 rightb_coord;
#endif

float gray(vec4 n)
{
	return (n.r + n.g + n.b)/3.0;
}

const float tau = 6.28318530718;

void main()
{

	vec4 color = IMG_THIS_PIXEL(inputImage);
	vec4 colorL = IMG_NORM_PIXEL(inputImage, left_coord);
	vec4 colorR = IMG_NORM_PIXEL(inputImage, right_coord);
	vec4 colorA = IMG_NORM_PIXEL(inputImage, above_coord);
	vec4 colorB = IMG_NORM_PIXEL(inputImage, below_coord);

	vec4 colorLA = IMG_NORM_PIXEL(inputImage, lefta_coord);
	vec4 colorRA = IMG_NORM_PIXEL(inputImage, righta_coord);
	vec4 colorLB = IMG_NORM_PIXEL(inputImage, leftb_coord);
	vec4 colorRB = IMG_NORM_PIXEL(inputImage, rightb_coord);



	float gx = (-1.0 * gray(colorLA)) + (-1.0 * gray(colorL)) + (-1.0 * gray(colorLB)) + (1.0 * gray(colorRA)) + (1.0 * gray(colorR)) + (1.0 * gray(colorRB));
	float gy = (1.0 * gray(colorLA)) + (1.0 * gray(colorA)) + (1.0 * gray(colorRA)) + (-1.0 * gray(colorRB)) + (-1.0 * gray(colorB)) + (-1.0 * gray(colorLB));

	float edge = pow(gx*gx + gy*gy,0.5);
	float brightness = (color.r + color.g + color.b) / 3.0;

	vec2 tc = isf_FragNormCoord;
	vec2 modifiedCenter = vec2(0.5,0.5);
	float r = distance(modifiedCenter, tc);
	float a = atan ((tc.y-modifiedCenter.y),(tc.x-modifiedCenter.x));
	
	//	adjust the angle and radius based on the brightness and edge level
	if (invert_map)	{
		edge = 1.0 - edge;
	}
	r = r + intensity * (1.0-edge) * (brightness - 0.5);
	//a = a + intensity * pow(1.0+edge,brightness);
	
	tc.x = r * cos(a) + 0.5;
	tc.y = r * sin(a) + 0.5;
	
	vec4 final = IMG_NORM_PIXEL(inputImage, tc);
	
	gl_FragColor = final;
}'''

VS = r'''#if __VERSION__ <= 120
varying vec2 left_coord;
varying vec2 right_coord;
varying vec2 above_coord;
varying vec2 below_coord;

varying vec2 lefta_coord;
varying vec2 righta_coord;
varying vec2 leftb_coord;
varying vec2 rightb_coord;
#else
out vec2 left_coord;
out vec2 right_coord;
out vec2 above_coord;
out vec2 below_coord;

out vec2 lefta_coord;
out vec2 righta_coord;
out vec2 leftb_coord;
out vec2 rightb_coord;
#endif


void main()
{
	isf_vertShaderInit();
	vec2 texc = vec2(isf_FragNormCoord[0],isf_FragNormCoord[1]);
	vec2 d = 1.0/RENDERSIZE;
	
	left_coord = clamp(vec2(texc.xy + vec2(-d.x , 0)),0.0,1.0);
	right_coord = clamp(vec2(texc.xy + vec2(d.x , 0)),0.0,1.0);
	above_coord = clamp(vec2(texc.xy + vec2(0,d.y)),0.0,1.0);
	below_coord = clamp(vec2(texc.xy + vec2(0,-d.y)),0.0,1.0);

	lefta_coord = clamp(vec2(texc.xy + vec2(-d.x , d.x)),0.0,1.0);
	righta_coord = clamp(vec2(texc.xy + vec2(d.x , d.x)),0.0,1.0);
	leftb_coord = clamp(vec2(texc.xy + vec2(-d.x , -d.x)),0.0,1.0);
	rightb_coord = clamp(vec2(texc.xy + vec2(d.x , -d.x)),0.0,1.0);
}'''


class IsfEdgeDistort(GlslEffect):
    name = 'isf_edge_distort'
    label = 'Edge Distort'
    description = 'Edge Distort (Distortion Effect).'
    dialect = 'isf'
    GLSL = GLSL
    VS = VS
    stage = 'warp'
    scale = 0.35
    params = effect_params({
        "p_intensity": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 0.2,
            "x-ui": {
                "label": "Intensity",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "intensity"
            }
        },
        "invert_map": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Invert map"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "invert_map"
            }
        }
    })
