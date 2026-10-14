"""Sepia Tone: Sepia Tone (Color Effect, Retro).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Sepia%20Tone.fs
Author: by VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Color Effect",
        "Retro"
    ],
    "CREDIT": "by VIDVOX",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0.8,
            "MAX": 1.2,
            "MIN": 0.8,
            "NAME": "contrast",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2"
}
*/


//	Adapted from https://www.omniref.com/ruby/gems/essytas/0.0.1/files/lib/glsl/sepia.frag

void main() {

	vec4 rawColor = IMG_THIS_PIXEL(inputImage);
	vec4 color = rawColor;
	vec4 sepia1 = vec4( 0.2, 0.05, 0.0, 1.0 );    
	vec4 sepia2 = vec4( 1.0, 0.9, 0.5, 1.0 );
	float sepiaMix = dot(vec3(0.3, 0.59, 0.11), color.rgb);
	color = mix(color, vec4(sepiaMix), 0.5);
	vec4 sepia = mix(sepia1, sepia2, sepiaMix);
	sepia = vec4( min( vec3( 1.0 ), sepia.rgb ), color.a );
	
	float bright = 0.05;
	sepia = sepia + vec4(bright, bright, bright, 0.0);
	sepia.rgb = ((vec3(2.0) * (sepia.rgb - vec3(0.5))) * vec3(contrast) / vec3(2.0)) + vec3(0.5);
	sepia.a = rawColor.a;
	gl_FragColor = sepia;

}
'''


class IsfSepiaTone(GlslEffect):
    name = 'isf_sepia_tone'
    label = 'Sepia Tone'
    description = 'Sepia Tone (Color Effect, Retro).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'colour'
    params = effect_params({
        "contrast": {
            "type": "number",
            "minimum": 0.8,
            "maximum": 1.2,
            "default": 0.8,
            "x-ui": {
                "label": "Contrast",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "contrast"
            }
        }
    })
