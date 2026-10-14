"""CMYK Halftone: CMYK Halftone (Halftone Effect, Retro).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/CMYK%20Halftone.fs
Author: by zoidberg
License: MIT
license_class: permissive
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Vidvox/ISF-Files LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Halftone Effect",
        "Retro"
    ],
    "CREDIT": "by zoidberg",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 45,
            "MAX": 256,
            "MIN": 1,
            "NAME": "gridSize",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.15,
            "MAX": 1,
            "MIN": 0,
            "NAME": "smoothing",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2"
}
*/

const vec4		gridRot = vec4(15.0, 45.0, 0.0, 75.0);




vec4 RGBAtoCMYK(vec4 inColor)
{
	vec4		ret;
	ret.w = 1.0 - max(max(inColor.x, inColor.y), inColor.z);
	ret.x = (1.0-inColor.x-ret.w)/(1.0-ret.w);
	ret.y = (1.0-inColor.y-ret.w)/(1.0-ret.w);
	ret.z = (1.0-inColor.z-ret.w)/(1.0-ret.w);
	return ret;
}
vec4 CMYKtoRGBA(vec4 inColor)
{
	vec4		ret;
	ret.xyz = (1.0-inColor.xyz)*(1.0-inColor.w);
	ret.w = 1.0;
	return ret;
}

void main() {
	//	a halftone is an overlapping series of grids of dots
	//	each grid of dots is rotated by a different amount
	//	the size of the dots determines the colors. the shape of the dot should never change (always be a dot with regular edges)
	
	vec4		cmykAmounts = vec4(0.0);
	
	//	for each of the channels (i) of CMYK...
	for (int i=0; i<4; ++i)	{
		//	figure out the rotation of the grid in radians
		float		rotRad = radians(gridRot[i]);
		//	the grids are rotated counter-clockwise- to find the nearest dot, take the fragment pixel loc, 
		//	rotate it clockwise, and split by the grid to find the center of the dot. then rotate this 
		//	coord counter-clockwise to yield the location of the center of the dot in pixel coords local to the render space
		mat2		ccTrans = mat2(vec2(cos(rotRad), sin(rotRad)), vec2(-1.0*sin(rotRad), cos(rotRad)));
		mat2		cTrans = mat2(vec2(cos(rotRad), -1.0*sin(rotRad)), vec2(sin(rotRad), cos(rotRad)));
		
		//	render loc -> grid loc -> grid dot loc -> grid dot loc in render coords -> pixel color under grid dot loc
		vec2		gridFragLoc = cTrans * gl_FragCoord.xy;
		vec2		gridDotLoc = vec2(floor(gridFragLoc.x/gridSize)*gridSize, floor(gridFragLoc.y/gridSize)*gridSize);
		gridDotLoc = gridDotLoc + vec2(gridSize/2.0);
		vec2		renderDotLoc = ccTrans * gridDotLoc;
		vec4		renderDotImageColorRGB = IMG_PIXEL(inputImage, renderDotLoc);
		vec4		renderDotImageColorCMYK = RGBAtoCMYK(renderDotImageColorRGB);
		
		float		channelAmount = renderDotImageColorCMYK[i];
		float		dotRadius = channelAmount * (gridSize/2.0);
		float		fragDistanceToGridCenter = distance(gl_FragCoord.xy, renderDotLoc);
		//	the amount of the channel depends on the distance to the center of the grid, the size of the dot, and smoothing
		float		smoothDist = smoothing * (gridSize/6.0);
		cmykAmounts[i] += smoothstep(dotRadius, dotRadius-(dotRadius*smoothing), fragDistanceToGridCenter);
	}
	
	gl_FragColor = CMYKtoRGBA(cmykAmounts);
}'''


class IsfCmykHalftone(GlslEffect):
    name = 'isf_cmyk_halftone'
    label = 'CMYK Halftone'
    description = 'CMYK Halftone (Halftone Effect, Retro).'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'colour'
    scale = 0.7
    params = effect_params({
        "grid_size": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 256.0,
            "default": 45.0,
            "x-ui": {
                "label": "Grid Size",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "gridSize"
            }
        },
        "smoothing": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.15,
            "x-ui": {
                "label": "Smoothing",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "smoothing"
            }
        }
    })
