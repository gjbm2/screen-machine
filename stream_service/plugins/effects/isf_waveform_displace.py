"""Waveform Displace: Displaces image with audio waveform

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Waveform%20Displace.fs
Author: icalvin102 (calvin@icalvin.de)
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Audio Visualizer",
        "Distortion Effect"
    ],
    "CREDIT": "icalvin102 (calvin@icalvin.de)",
    "DESCRIPTION": "Displaces image with audio waveform",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "LABEL": "Audio Waveform",
            "NAME": "audio",
            "TYPE": "audio"
        },
        {
            "DEFAULT": 0.1,
            "LABEL": "Displace X",
            "MAX": 1,
            "MIN": 0,
            "NAME": "displaceX",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.1,
            "LABEL": "Displace Y",
            "MAX": 1,
            "MIN": 0,
            "NAME": "displaceY",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.5,
            "LABEL": "Detail X",
            "MAX": 1,
            "MIN": 0,
            "NAME": "detailX",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.5,
            "LABEL": "Detail Y",
            "MAX": 1,
            "MIN": 0,
            "NAME": "detailY",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2",
    "PASSES": [
        {
            "DESCRIPTION": "Renderpass 0"
        }
    ]
}
*/

void main()	{

	vec4 inputPixelColor;
	vec2 uv = isf_FragNormCoord.xy;
	vec2 waveLoc = fract((uv)*vec2(detailX, detailY));

	vec2 wave = vec2(IMG_NORM_PIXEL(audio, vec2(waveLoc.x, 0.0)).r, IMG_NORM_PIXEL(audio, vec2(waveLoc.y, 1.0)).r)-.5;
	wave *= vec2(displaceY, displaceX);

	inputPixelColor = IMG_NORM_PIXEL(inputImage, uv + wave.yx);

	gl_FragColor = inputPixelColor;
}
'''


class IsfWaveformDisplace(GlslEffect):
    name = 'isf_waveform_displace'
    label = 'Waveform Displace'
    description = 'Displaces image with audio waveform'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    params = effect_params({
        "displace_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.1,
            "x-ui": {
                "label": "Displace X",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "displaceX"
            }
        },
        "displace_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.1,
            "x-ui": {
                "label": "Displace Y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "displaceY"
            }
        },
        "detail_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Detail X",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "detailX"
            }
        },
        "detail_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Detail Y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "detailY"
            }
        }
    })
