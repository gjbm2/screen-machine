"""FFT Filled Waveform: Visualizes an FFT analysis image with custom set colors for frequency domain

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/FFT%20Filled%20Waveform.fs
Author: by VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*{
	"DESCRIPTION": "Visualizes an FFT analysis image with custom set colors for frequency domain",
	"CREDIT": "by VIDVOX",
	"ISFVSN": "2",
	"CATEGORIES": [
		"Audio Visualizer"
	],
	"INPUTS": [
		{
			"NAME": "fftImage",
			"TYPE": "audioFFT"
		},
		{
			"NAME": "strokeSize",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 0.25,
			"DEFAULT": 0.1
		},
		{
			"NAME": "gain",
			"TYPE": "float",
			"MIN": 1.0,
			"MAX": 5.0,
			"DEFAULT": 1.0
		},
		{
			"NAME": "minRange",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 0.0
		},
		{
			"NAME": "maxRange",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 1.0
		},
		{
			"NAME": "topColor",
			"TYPE": "color",
			"DEFAULT": [
				0.0,
				0.0,
				0.0,
				0.0
			]
		},
		{
			"NAME": "bottomColor",
			"TYPE": "color",
			"DEFAULT": [
				0.0,
				0.5,
				0.9,
				1.0
			]
		},
		{
			"NAME": "strokeColor",
			"TYPE": "color",
			"DEFAULT": [
				0.25,
				0.25,
				0.25,
				1.0
			]
		}
	]
}*/



void main() {
	
	vec2 loc = isf_FragNormCoord;
	
	//	the fftImage is 256 steps
	loc.x = loc.x * abs(maxRange - minRange) + minRange;
	
	vec4 fft = IMG_NORM_PIXEL(fftImage, vec2(loc.x,0.5));
	float fftVal = gain * (fft.r + fft.g + fft.b) / 3.0;
	if (loc.y > fftVal)
		fft = topColor;
	else
		fft = bottomColor;
	if ((strokeSize > 0.0) && (abs(fftVal - loc.y) < strokeSize))	{
		fft = mix(strokeColor, fft, abs(fftVal - loc.y) / strokeSize);
	}
	
	//(smoothstep(0.0, stroke, abs(fftVal - loc.y))) * strokeColor);
	gl_FragColor = fft;
}'''


class IsfFftFilledWaveform(GlslSource):
    name = 'isf_fft_filled_waveform'
    label = 'FFT Filled Waveform'
    description = 'Visualizes an FFT analysis image with custom set colors for frequency domain'
    dialect = 'isf'
    GLSL = GLSL
    params = scene_params({
        "stroke_size": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.25,
            "default": 0.1,
            "x-ui": {
                "label": "Stroke Size",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "strokeSize"
            }
        },
        "gain": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 5.0,
            "default": 1.0,
            "x-ui": {
                "label": "Gain",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "gain"
            }
        },
        "min_range": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Min Range",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "minRange"
            }
        },
        "max_range": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "Max Range",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "maxRange"
            }
        },
        "top_color": {
            "type": "string",
            "format": "color",
            "default": "#000000",
            "x-ui": {
                "label": "Top Color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "topColor",
                "color": 4,
                "alpha": 0.0
            }
        },
        "bottom_color": {
            "type": "string",
            "format": "color",
            "default": "#0080e6",
            "x-ui": {
                "label": "Bottom Color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "bottomColor",
                "color": 4,
                "alpha": 1.0
            }
        },
        "stroke_color": {
            "type": "string",
            "format": "color",
            "default": "#404040",
            "x-ui": {
                "label": "Stroke Color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "strokeColor",
                "color": 4,
                "alpha": 1.0
            }
        }
    }, render_scale=0.5)
