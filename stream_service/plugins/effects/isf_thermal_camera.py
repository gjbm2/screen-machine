"""Thermal Camera: Thermal Camera (Stylize, Color Effect).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Thermal%20Camera.fs
Author: by VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Stylize",
        "Color Effect"
    ],
    "CREDIT": "by VIDVOX",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        }
    ],
    "ISFVSN": "2"
}
*/


//	partly adapted from http://coding-experiments.blogspot.com/2010/10/thermal-vision-pixel-shader.html
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


void main ()	{
	//vec4 pixcol = IMG_THIS_PIXEL(inputImage);
	vec4 colors[9];
	//	8 color stages with variable ranges to get this to look right
	//	black, purple, blue, cyan, green, yellow, orange, red, red
	colors[0] = vec4(0.0,0.0,0.0,1.0);
	colors[1] = vec4(0.272,0.0,0.4,1.0);	//	dark deep purple, (RGB: 139, 0, 204)
	colors[2] = vec4(0.0,0.0,1.0,1.0);		//	full blue
	colors[3] = vec4(0.0,1.0,1.0,1.0);		//	cyan
	colors[4] = vec4(0.0,1.0,0.0,1.0);		//	green
	colors[5] = vec4(0.0,1.0,0.0,1.0);		//	green
	colors[6] = vec4(1.0,1.0,0.0,1.0);		//	yellow
	colors[7] = vec4(1.0,0.5,0.0,1.0);		//	orange
	colors[8] = vec4(1.0,0.0,0.0,1.0);		//	red
	
	vec4 color = IMG_THIS_NORM_PIXEL(inputImage);
	vec4 colorL = IMG_NORM_PIXEL(inputImage, left_coord);
	vec4 colorR = IMG_NORM_PIXEL(inputImage, right_coord);
	vec4 colorA = IMG_NORM_PIXEL(inputImage, above_coord);
	vec4 colorB = IMG_NORM_PIXEL(inputImage, below_coord);

	vec4 colorLA = IMG_NORM_PIXEL(inputImage, lefta_coord);
	vec4 colorRA = IMG_NORM_PIXEL(inputImage, righta_coord);
	vec4 colorLB = IMG_NORM_PIXEL(inputImage, leftb_coord);
	vec4 colorRB = IMG_NORM_PIXEL(inputImage, rightb_coord);

	vec4 avg = (color + colorL + colorR + colorA + colorB + colorLA + colorRA + colorLB + colorRB) / 9.0;
	
	const vec4 	lumacoeff = vec4(0.2126, 0.7152, 0.0722, 0.0);
	float		lum = dot(avg, lumacoeff);
	//float lum = (avg.r+avg.g+avg.b)/3.0;
	//float lum = dot(vec3(0.30, 0.59, 0.11), avg.rgb);
	lum = pow(lum,1.4);

	int ix = 0;
	float range = 1.0 / 8.0;
	
	//	orange to red
	if (lum > range * 7.0)	{
		ix = 7;
	}
	//	yellow to orange
	else if (lum > range * 6.0)	{
		ix = 6;
	}
	//	green to yellow
	else if (lum > range * 5.0)	{
		ix = 5;
	}
	//	green to green
	else if (lum > range * 4.0)	{
		ix = 4;
	}
	//	cyan to green
	else if (lum > range * 3.0)	{
		ix = 3;
	}
	//	blue to cyan
	else if (lum > range * 2.0)	{
		ix = 2;
	}
	// purple to blue
	else if (lum > range)	{
		ix = 1;
	}
	
	vec4 thermal = mix(colors[ix],colors[ix+1],(lum-float(ix)*range)/range);
	gl_FragColor = thermal;

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


class IsfThermalCamera(GlslEffect):
    name = 'isf_thermal_camera'
    label = 'Thermal Camera'
    description = 'Thermal Camera (Stylize, Color Effect).'
    dialect = 'isf'
    GLSL = GLSL
    VS = VS
    stage = 'colour'
    scale = 0.45
    params = effect_params({})
