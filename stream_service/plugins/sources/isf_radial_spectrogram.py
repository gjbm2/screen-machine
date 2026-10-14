"""Radial Spectrogram: Generates a radial audiospectrogram from fft-input

Source: https://github.com/icalvin102/ISF/blob/HEAD/RadialSpectrogram.fs
Author: icalvin102 (calvin@icalvin.de)
License: MIT
license_class: permissive
Imported verbatim from GLSL (icalvin102/ISF); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository icalvin102/ISF LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*
{
  "CATEGORIES" : [
    "icalvin102"
  ],
	"DESCRIPTION" : "Generates a radial audiospectrogram from fft-input",
  "ISFVSN" : "2",
  "INPUTS" : [
    {
      "NAME" : "audioFFT",
      "TYPE" : "audioFFT",
      "LABEL" : "AudioFFT"
    },
    {
      "NAME" : "size",
      "TYPE" : "float",
      "MAX" : 0.050000000000000003,
      "DEFAULT" : 0.001,
      "MIN" : 1.0000000000000001e-05,
      "LABEL" : "Size"
    },
    {
      "NAME" : "feedbackOpacity",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0.98999999999999999,
      "LABEL" : "Feedback Opacity",
      "MIN" : 0.69999999999999996
    },
    {
      "NAME" : "low",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 0,
      "LABEL" : "Lowest Frequency",
      "MIN" : 0
    },
    {
      "NAME" : "high",
      "TYPE" : "float",
      "MAX" : 1,
      "DEFAULT" : 1,
      "LABEL" : "Heighest Frequency",
      "MIN" : 0
    }
  ],
  "PASSES" : [
  	{
	  	"PERSISTENT" : true,
			"WIDTH" : "1",
			"HEIGHT" : "1",
			"TARGET" : "time",
			"FLOAT" : true
		},
		{
      "TARGET" : "buff",
      "PERSISTENT" : true,
      "FLOAT" : true
    },
    {

    }
  ],
  "CREDIT" : "icalvin102 (calvin@icalvin.de)"
}
*/

void main()	{
	vec4 inputPixelColor = IMG_THIS_PIXEL(buff);
	float t = IMG_PIXEL(time, vec2(0.0,0.0)).r;
	if(PASSINDEX == 0){
		gl_FragColor = vec4(t+size);
		return;
	}
	if(PASSINDEX == 1){
		vec2 dir = vec2(sin(t), cos(t));
		vec2 co = (isf_FragNormCoord - vec2(0.5,0.5)) * 2.0;
 		float radius=length(co);
 		inputPixelColor *= feedbackOpacity;
 		if( dot(dir, normalize(co)) >= cos(size) && radius <= 1.0){
			inputPixelColor = IMG_NORM_PIXEL(audioFFT, vec2((1.0-radius) * abs(high - low) + low, .5));
 		}
 		inputPixelColor.a = 1.0;
	}
	gl_FragColor = inputPixelColor;
}
'''


class IsfRadialSpectrogram(GlslSource):
    name = 'isf_radial_spectrogram'
    label = 'Radial Spectrogram'
    description = 'Generates a radial audiospectrogram from fft-input'
    dialect = 'isf'
    GLSL = GLSL
    params = scene_params({
        "size": {
            "type": "number",
            "minimum": 1e-05,
            "maximum": 0.05,
            "default": 0.001,
            "x-ui": {
                "label": "Size",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "size"
            }
        },
        "feedback_opacity": {
            "type": "number",
            "minimum": 0.7,
            "maximum": 1.0,
            "default": 0.99,
            "x-ui": {
                "label": "Feedback Opacity",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "feedbackOpacity"
            }
        },
        "low": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Lowest Frequency",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "low"
            }
        },
        "high": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "Heighest Frequency",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "high"
            }
        }
    }, render_scale=0.5)
