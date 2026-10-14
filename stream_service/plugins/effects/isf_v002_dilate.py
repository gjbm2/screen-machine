"""V002 Dilate: messed up version of v002 dilate

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/v002%20Dilate.fs
Author: by carter rosenberg
License: CC-BY-NC-SA-3.0
license_class: noncommercial
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
v002 port: v002 sources are CC-BY-NC-SA-3.0.
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
	"DESCRIPTION": "messed up version of v002 dilate",
	"CREDIT": "by carter rosenberg",
	"ISFVSN": "2",
	"CATEGORIES": [
		"Blur", "v002"
	],
	"INPUTS": [
		{
			"NAME": "inputImage",
			"TYPE": "image"
		},
		{
			"NAME": "amount",
			"TYPE": "float",
			"MIN": 0.0,
			"MAX": 0.1,
			"DEFAULT": 0.01
		}
	]
}*/

#if __VERSION__ <= 120
varying vec2 texcoord0;
varying vec2 texcoord1;
varying vec2 texcoord2;
varying vec2 texcoord3;
varying vec2 texcoord4;
varying vec2 texcoord5;
varying vec2 texcoord6;
varying vec2 texcoord7;
#else
in vec2 texcoord0;
in vec2 texcoord1;
in vec2 texcoord2;
in vec2 texcoord3;
in vec2 texcoord4;
in vec2 texcoord5;
in vec2 texcoord6;
in vec2 texcoord7;
#endif

void main()
{

	vec4 dilate = IMG_NORM_PIXEL(inputImage, 0.5 * (texcoord3 + texcoord4));
	
	dilate = max(dilate, IMG_NORM_PIXEL(inputImage, texcoord0));
	dilate = max(dilate, IMG_NORM_PIXEL(inputImage, texcoord1));
	dilate = max(dilate, IMG_NORM_PIXEL(inputImage, texcoord2));
	dilate = max(dilate, IMG_NORM_PIXEL(inputImage, texcoord3));
	dilate = max(dilate, IMG_NORM_PIXEL(inputImage, texcoord4));
	dilate = max(dilate, IMG_NORM_PIXEL(inputImage, texcoord5));
	dilate = max(dilate, IMG_NORM_PIXEL(inputImage, texcoord6));
	dilate = max(dilate, IMG_NORM_PIXEL(inputImage, texcoord7));

	gl_FragColor = dilate;
}'''

VS = r'''
#if __VERSION__ <= 120
varying vec2 texcoord0;
varying vec2 texcoord1;
varying vec2 texcoord2;
varying vec2 texcoord3;
varying vec2 texcoord4;
varying vec2 texcoord5;
varying vec2 texcoord6;
varying vec2 texcoord7;
#else
out vec2 texcoord0;
out vec2 texcoord1;
out vec2 texcoord2;
out vec2 texcoord3;
out vec2 texcoord4;
out vec2 texcoord5;
out vec2 texcoord6;
out vec2 texcoord7;
#endif

void main()
{
	isf_vertShaderInit();
	// perform standard transform on vertex
	//gl_Position = ftransform();

	// transform texcoord        
	vec2 texcoord = vec2(isf_FragNormCoord[0],isf_FragNormCoord[1]);

	// get sample positions
	texcoord0 = texcoord + vec2(-amount, -amount);
	texcoord1 = texcoord + vec2( 0,      -amount);
	texcoord2 = texcoord + vec2( amount, -amount);
	texcoord3 = texcoord + vec2(-amount,  0);
	texcoord4 = texcoord + vec2( amount,  0);
	texcoord5 = texcoord + vec2(-amount,  amount);
	texcoord6 = texcoord + vec2( 0,       amount);
	texcoord7 = texcoord + vec2( amount,  amount);
}
'''


class IsfV002Dilate(GlslEffect):
    name = 'isf_v002_dilate'
    label = 'V002 Dilate'
    description = 'messed up version of v002 dilate'
    dialect = 'isf'
    GLSL = GLSL
    VS = VS
    stage = 'stylise'
    scale = 0.45
    params = effect_params({
        "p_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.1,
            "default": 0.01,
            "x-ui": {
                "label": "Amount",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "amount"
            }
        }
    })
