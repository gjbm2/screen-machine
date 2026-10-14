"""Waveform Displace audio As Image: Displaces image with audio waveform

Source: https://github.com/icalvin102/ISF/blob/HEAD/WaveformDisplace_audioAsImage.fs
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
  "DESCRIPTION" : "Displaces image with audio waveform",
  "ISFVSN" : "2",
  "INPUTS" : [
    {
      "NAME" : "inputImage",
      "TYPE" : "image"
    },
    {
      "NAME" : "audio",
      "TYPE" : "image",
      "LABEL" : "Audio Waveform"
    },
    {
      "NAME" : "displaceX",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0.10000000000000001,
      "MIN" : 0,
      "LABEL" : "Displace X"
    },
    {
      "NAME" : "displaceY",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0.10000000000000001,
      "MIN" : 0,
      "LABEL" : "Displace Y"
    },
    {
      "NAME" : "detailX",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0.5,
      "MIN" : 0,
      "LABEL" : "Detail X"
    },
    {
      "NAME" : "detailY",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0.5,
      "MIN" : 0,
      "LABEL" : "Detail Y"
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


class IsfWaveformDisplaceAudioAsImage(GlslEffect):
    name = 'isf_waveform_displace_audio_as_image'
    label = 'Waveform Displace audio As Image'
    description = 'Displaces image with audio waveform'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    scale = 0.7
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
