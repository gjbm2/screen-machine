"""V002 Vignette: V002 Vignette (Film, v002).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/v002%20Vignette.fs
Author: by v002
License: CC-BY-NC-SA-3.0
license_class: noncommercial
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
v002 port: v002 sources are CC-BY-NC-SA-3.0.
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Film",
        "v002"
    ],
    "CREDIT": "by v002",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0.5,
            "MAX": 1,
            "MIN": 0,
            "NAME": "vignette",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "MAX": 1,
            "MIN": 0,
            "NAME": "vignetteEdge",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "MAX": 1,
            "MIN": 0,
            "NAME": "vignetteMix",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2"
}
*/


//	Based on v002 vignette – https://github.com/v002/v002-Film-Effects/



// create a black and white oval about the center of our image for our vignette
vec4 vignetteFucntion(vec2 normalizedTexcoord, float vignetteedge, float vignetteMix)
{
	normalizedTexcoord = 2.0 * normalizedTexcoord - 1.0; // - 1.0 to 1.0
	float r = length(normalizedTexcoord);
	vec4 vignette = (vec4(smoothstep(0.0, 1.0, pow(clamp(r - vignetteMix, 0.0, 1.0), 1.0 + vignetteedge * 10.0))));
	return clamp(1.0 - vignette, 0.0, 1.0);
}



void main (void) 
{ 		
	vec2 normcoord = vec2(isf_FragNormCoord[0],isf_FragNormCoord[1]);


	// make a vignette around our borders.
	vec4 vignetteResult = vignetteFucntion(normcoord, vignetteEdge, vignetteMix);

	// sharpen via unsharp mask (subtract image from blured image)
	vec4 input0 = IMG_THIS_PIXEL(inputImage);

	gl_FragColor = mix(input0,vignetteResult * input0, vignette);		
} '''


class IsfV002Vignette(GlslEffect):
    name = 'isf_v002_vignette'
    label = 'V002 Vignette'
    description = 'V002 Vignette (Film, v002).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'overlay'
    params = effect_params({
        "vignette": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Vignette",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "vignette"
            }
        },
        "vignette_edge": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Vignette Edge",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "vignetteEdge"
            }
        },
        "vignette_mix": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Vignette Mix",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "vignetteMix"
            }
        }
    })
