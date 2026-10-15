"""FFT Spectrogram: Buffers the incoming FFTs for timed display

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/FFT%20Spectrogram.fs
Author: by VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*{
	"DESCRIPTION": "Buffers the incoming FFTs for timed display",
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
			"NAME": "clear",
			"TYPE": "event"
		},
		{
			"NAME": "gain",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 2.0,
			"DEFAULT":1.0
		},
		{
			"NAME": "range",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 1.0,
			"DEFAULT": 1.0
		},
		{
			"NAME": "axis_scale",
			"TYPE": "float",
			"MIN": 1.0,
			"MAX": 6.0,
			"DEFAULT": 1.25
		},
		{
			"NAME": "lumaMode",
			"TYPE": "bool",
			"DEFAULT": 0.0
		},
		{
			"NAME": "color1",
			"TYPE": "color",
			"DEFAULT": [
				0.0,
				0.5,
				1.0,
				1.0
			]
		},
		{
			"NAME": "color2",
			"TYPE": "color",
			"DEFAULT": [
				1.0,
				0.0,
				0.0,
				1.0
			]
		},
		{
			"NAME": "color3",
			"TYPE": "color",
			"DEFAULT": [
				0.0,
				1.0,
				0.0,
				1.0
			]
		}
	],
	"PASSES": [
		{
			"TARGET": "fftValues",
			"DESCRIPTION": "This buffer stores all of the previous fft values",
			"HEIGHT": "512",
			"FLOAT": true,
			"PERSISTENT": true
		},
		{
	
		}
	]
    
}*/

void main()
{
	//    first pass- read the value from the fft image, store it in the "fftValues" persistent buffer
	if (PASSINDEX==0)    {
		//    the first column of pixels is copied from the newly received fft image
		if (clear)	{
			gl_FragColor = vec4(0.0);
		}
		else if (gl_FragCoord.x<1.0)    {
			vec2		loc = vec2(isf_FragNormCoord.y, isf_FragNormCoord.x);
			vec4		rawColor = IMG_NORM_PIXEL(fftImage, loc);
			gl_FragColor = rawColor;
		}
		//    all other columns of pixels come from the "fftValues" persistent buffer (we're scrolling)
		else    {
			gl_FragColor = IMG_PIXEL(fftValues, vec2(gl_FragCoord.x-1.0, gl_FragCoord.y));
		}
	}
	//    second pass- read from the buffer of raw values, apply gain/range and colors
	else if (PASSINDEX==1)    {
		vec2		loc = vec2(isf_FragNormCoord.x, pow(isf_FragNormCoord.y*range, axis_scale));
		vec4		rawColor = IMG_NORM_PIXEL(fftValues, loc);
	
		rawColor = rawColor * vec4(gain);
	
		float		mixVal = 0.0;
		
		if (lumaMode)	{
			float		locus_1 = 0.20;
			float		locus_2 = 0.50;
			float		locus_3 = 0.75;
	
			if (rawColor.r < locus_1)    {
				mixVal = (rawColor.r)/(locus_1);
				gl_FragColor = mix(vec4(0,0,0,0), color1, mixVal);
			}
			else if (rawColor.r>=locus_1 && rawColor.r<locus_2)    {
				mixVal = (rawColor.r - locus_1)/(locus_2 - locus_1);
				gl_FragColor = mix(color1, color2, mixVal);
			}
			else if (rawColor.r>=locus_2 && rawColor.r<locus_3)    {
				mixVal = (rawColor.r - locus_2)/(locus_3 - locus_2);
				gl_FragColor = mix(color2, color3, mixVal);
			}
			else if (rawColor.r>=locus_3)    {
				mixVal = (rawColor.r - locus_3);
				gl_FragColor = mix(color3, vec4(1,1,1,1), mixVal);
			}
		}
		else	{
			float		locus_1 = 0.25;
			float		locus_2 = 0.5;
			float		locus_3 = 0.75;
	
			if (loc.y < locus_1)    {
				gl_FragColor = rawColor.r * color1;
			}
			else if (loc.y>=locus_1 && loc.y<locus_2)    {
				mixVal = (loc.y - locus_1)/(locus_2 - locus_1);
				gl_FragColor = rawColor.r * mix(color1, color2, mixVal);
			}
			else if (loc.y>=locus_2 && loc.y<locus_3)    {
				mixVal = (loc.y - locus_2)/(locus_3 - locus_2);
				gl_FragColor = rawColor.r * mix(color2, color3, mixVal);
			}
			else if (loc.y > locus_3)    {
				gl_FragColor = rawColor.r * color3;
			}
		}
	}
}'''


class IsfFftSpectrogram(GlslSource):
    name = 'isf_fft_spectrogram'
    label = 'FFT Spectrogram'
    description = 'Buffers the incoming FFTs for timed display'
    dialect = 'isf'
    GLSL = GLSL
    events = ('clear',)
    params = scene_params({
        "gain": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 1.0,
            "x-ui": {
                "label": "Gain",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "gain"
            }
        },
        "range": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "Range",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "range"
            }
        },
        "axis_scale": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 6.0,
            "default": 1.25,
            "x-ui": {
                "label": "Axis scale",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "axis_scale"
            }
        },
        "luma_mode": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Luma Mode"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "lumaMode"
            }
        },
        "color1": {
            "type": "string",
            "format": "color",
            "default": "#0080ff",
            "x-ui": {
                "label": "Color1",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "color1",
                "color": 4,
                "alpha": 1.0
            }
        },
        "color2": {
            "type": "string",
            "format": "color",
            "default": "#ff0000",
            "x-ui": {
                "label": "Color2",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "color2",
                "color": 4,
                "alpha": 1.0
            }
        },
        "color3": {
            "type": "string",
            "format": "color",
            "default": "#00ff00",
            "x-ui": {
                "label": "Color3",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "color3",
                "color": 4,
                "alpha": 1.0
            }
        }
    }, render_scale=0.5)
