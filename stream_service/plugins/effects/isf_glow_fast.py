"""Glow Fast: Glow Fast (Stylize).

Source: https://github.com/Vidvox/ISF-Files/blob/395072d48b3ce7351ccb20a5fda54470591324df/ISF/Glow-Fast.fs
Author: by VIDVOX
License: CC-BY-NC-SA-3.0
license_class: noncommercial
Imported verbatim from GLSL (Vidvox ISF-Files); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
v002 port: v002 sources are CC-BY-NC-SA-3.0.
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Stylize"
    ],
    "CREDIT": "by VIDVOX",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 1,
            "MAX": 1,
            "MIN": 0,
            "NAME": "intensity",
            "TYPE": "float"
        },
        {
            "DEFAULT": 12,
            "MAX": 12,
            "MIN": 0,
            "NAME": "blurAmount",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2",
    "PASSES": [
        {
            "DESCRIPTION": "Pass 0",
            "HEIGHT": "floor($HEIGHT/2.0)",
            "TARGET": "halfSizeBaseRender",
            "WIDTH": "floor($WIDTH/2.0)"
        },
        {
            "DESCRIPTION": "Pass 1",
            "HEIGHT": "floor($HEIGHT/4.0)",
            "TARGET": "quarterSizeBaseRender",
            "WIDTH": "floor($WIDTH/4.0)"
        },
        {
            "DESCRIPTION": "Pass 2",
            "HEIGHT": "floor($HEIGHT/8.0)",
            "TARGET": "eighthSizeBaseRender",
            "WIDTH": "floor($WIDTH/8.0)"
        },
        {
            "DESCRIPTION": "Pass 3",
            "HEIGHT": "floor($HEIGHT/4.0)",
            "TARGET": "quarterGaussA",
            "WIDTH": "floor($WIDTH/4.0)"
        },
        {
            "DESCRIPTION": "Pass 4",
            "HEIGHT": "floor($HEIGHT/4.0)",
            "TARGET": "quarterGaussB",
            "WIDTH": "floor($WIDTH/4.0)"
        },
        {
            "DESCRIPTION": "Pass 5",
            "TARGET": "fullGaussA"
        },
        {
            "DESCRIPTION": "Pass 6",
            "TARGET": "fullGaussB"
        }
    ]
}
*/


//	original blur implementation as v002.blur in QC by anton marini and tom butterworth, ported by zoidberg

#if __VERSION__ <= 120
varying vec2		texOffsets[5];
#else
in vec2		texOffsets[5];
#endif


void main() {
	int			blurLevel = int(floor(blurAmount/6.0));
	float		blurLevelModulus = mod(blurAmount, 6.0);
	//	first three passes are just copying the input image into the buffer at varying sizes
	if (PASSINDEX==0)	{
		gl_FragColor = IMG_NORM_PIXEL(inputImage, isf_FragNormCoord);
	}
	else if (PASSINDEX==1)	{
		gl_FragColor = IMG_NORM_PIXEL(halfSizeBaseRender, isf_FragNormCoord);
	}
	else if (PASSINDEX==2)	{
		gl_FragColor = IMG_NORM_PIXEL(quarterSizeBaseRender, isf_FragNormCoord);
	}
	//	start reading from the previous stage- each two passes completes a gaussian blur, then 
	//	we increase the resolution & blur (the lower-res blurred image from the previous pass) again...
	else if (PASSINDEX == 3)	{
		vec4		sample0 = IMG_NORM_PIXEL(eighthSizeBaseRender,texOffsets[0]);
		vec4		sample1 = IMG_NORM_PIXEL(eighthSizeBaseRender,texOffsets[1]);
		vec4		sample2 = IMG_NORM_PIXEL(eighthSizeBaseRender,texOffsets[2]);
		vec4		sample3 = IMG_NORM_PIXEL(eighthSizeBaseRender,texOffsets[3]);
		vec4		sample4 = IMG_NORM_PIXEL(eighthSizeBaseRender,texOffsets[4]);
		//gl_FragColor = vec4((sample0 + sample1 + sample2).rgb / (3.0), 1.0);
		//gl_FragColor = vec4((sample0 + sample1 + sample2 + sample3 + sample4).rgb / (5.0), 1.0);
		vec4		final = vec4((sample0 + sample1 + sample2 + sample3 + sample4).rgba / (5.0));
		final = mix(sample0, final, intensity);
		gl_FragColor = final;
	}
	else if (PASSINDEX == 4)	{
		vec4		sample0 = IMG_NORM_PIXEL(quarterGaussA,texOffsets[0]);
		vec4		sample1 = IMG_NORM_PIXEL(quarterGaussA,texOffsets[1]);
		vec4		sample2 = IMG_NORM_PIXEL(quarterGaussA,texOffsets[2]);
		vec4		sample3 = IMG_NORM_PIXEL(quarterGaussA,texOffsets[3]);
		vec4		sample4 = IMG_NORM_PIXEL(quarterGaussA,texOffsets[4]);
		//gl_FragColor = vec4((sample0 + sample1 + sample2).rgb / (3.0), 1.0);
		//gl_FragColor = vec4((sample0 + sample1 + sample2 + sample3 + sample4).rgb / (5.0), 1.0);
		vec4		final = vec4((sample0 + sample1 + sample2 + sample3 + sample4).rgba / (5.0));
		final = mix(sample0, final, intensity);
		gl_FragColor = final;
	}
	//	...writes into the full-size
	else if (PASSINDEX == 5)	{
		vec4		sample0 = IMG_NORM_PIXEL(quarterGaussB,texOffsets[0]);
		vec4		sample1 = IMG_NORM_PIXEL(quarterGaussB,texOffsets[1]);
		vec4		sample2 = IMG_NORM_PIXEL(quarterGaussB,texOffsets[2]);
		//gl_FragColor =  vec4((sample0 + sample1 + sample2).rgb / (3.0), 1.0);
		vec4		final = vec4((sample0 + sample1 + sample2).rgba / (3.0));
		final = mix(sample0, final, intensity);
		gl_FragColor = final;
	}
	else if (PASSINDEX == 6)	{
		//	this is the last pass- calculate the blurred image as i have in previous passes, then mix it in with the full-size input image using the blur amount so i get a smooth transition into the blur at low blur levels
		vec4		sample0 = IMG_NORM_PIXEL(fullGaussA,texOffsets[0]);
		vec4		sample1 = IMG_NORM_PIXEL(fullGaussA,texOffsets[1]);
		vec4		sample2 = IMG_NORM_PIXEL(fullGaussA,texOffsets[2]);
		vec4		final = vec4((sample0 + sample1 + sample2).rgba / (3.0));
		vec4		original = IMG_NORM_PIXEL(inputImage,isf_FragNormCoord);
		final = mix(original, min((final*original),1.0), intensity);
		//	First do a gloom,
		//	then a bright / contrast tweak based on the amount
		//	then a bloom
		vec4		tmpColorA = final;
		float		bright = intensity*0.222;
		vec4		tmpColorB = tmpColorA + vec4(bright,bright,bright,bright);
		//	contrast
		float		contrast = 1.0 + intensity * (2.893);
		tmpColorA.rgba = ((vec4(2.0) * (tmpColorB.rgba - vec4(0.5))) * vec4(contrast) / vec4(2.0)) + vec4(0.5);
		final = min((tmpColorA + tmpColorA * original), 1.0);
		gl_FragColor = final;
		//if (blurLevel == 0)
		//	gl_FragColor = mix(IMG_NORM_PIXEL(inputImage,isf_FragNormCoord), blurredImg, (blurLevelModulus/6.1));
		//else
		//	gl_FragColor = blurredImg;
	}
	
}
'''

VS = r'''#if __VERSION__ <= 120
varying vec2		texOffsets[5];
#else
out vec2		texOffsets[5];
#endif

void main(void)	{
	//	load the main shader stuff
	isf_vertShaderInit();
	
	
	int			blurLevel = int(floor(blurAmount/6.0));
	float		blurLevelModulus = mod(blurAmount, 6.0);
	float		blurRadius = 0.0;
	float		blurRadiusInPixels = 0.0;
	//	first three passes are just drawing the texture- do nothing
	
	//	the next four passes do gaussion blurs on the various levels
	if (PASSINDEX==3 || PASSINDEX==5)	{
		float		pixelWidth = 1.0/RENDERSIZE.x;
		//	pass 3 is sampling 1/12, but writing to 1/4
		if (PASSINDEX==3)	{
			if (blurLevel==1)
				blurRadius = blurLevelModulus/1.5;
			else if (blurLevel>1)
				blurRadius = 4.0;
		}
		//	pass 5 is sampling 1/4 and writing to full-size
		else if (PASSINDEX==5)	{
			if (blurLevel==0)
				blurRadius = blurLevelModulus;
			else if (blurLevel>0)
				blurRadius = 6.0;
		}
		blurRadiusInPixels = pixelWidth * blurRadius;
		texOffsets[0] = isf_FragNormCoord;
		texOffsets[1] = (blurRadius==0.0) ? isf_FragNormCoord : clamp(vec2(isf_FragNormCoord[0]-blurRadiusInPixels, isf_FragNormCoord[1]),0.0,1.0);
		texOffsets[2] = (blurRadius==0.0) ? isf_FragNormCoord : clamp(vec2(isf_FragNormCoord[0]+blurRadiusInPixels, isf_FragNormCoord[1]),0.0,1.0);
		if (PASSINDEX==3)	{
			texOffsets[3] = (blurRadius==0.0) ? isf_FragNormCoord : clamp(vec2(isf_FragNormCoord[0]-(2.0*blurRadiusInPixels), isf_FragNormCoord[1]),0.0,1.0);
			texOffsets[4] = (blurRadius==0.0) ? isf_FragNormCoord : clamp(vec2(isf_FragNormCoord[0]+(2.0*blurRadiusInPixels), isf_FragNormCoord[1]),0.0,1.0);
		}
	}
	else if (PASSINDEX==4 || PASSINDEX==6)	{
		float		pixelHeight = 1.0/RENDERSIZE.y;
		//	pass 4 is sampling 1/12, but writing to 1/4
		if (PASSINDEX==4)	{
			if (blurLevel==1)
				blurRadius = blurLevelModulus/1.5;
			else if (blurLevel>1)
				blurRadius = 4.0;
		}
		//	pass 6 is sampling 1/4 and writing to full-size
		else if (PASSINDEX==6)	{
			if (blurLevel==0)
				blurRadius = blurLevelModulus;
			else if (blurLevel>0)
				blurRadius = 6.0;
		}
		blurRadiusInPixels = pixelHeight * blurRadius;
		texOffsets[0] = isf_FragNormCoord;
		texOffsets[1] = (blurRadius==0.0) ? isf_FragNormCoord : clamp(vec2(isf_FragNormCoord[0], isf_FragNormCoord[1]-blurRadiusInPixels),0.0,1.0);
		texOffsets[2] = (blurRadius==0.0) ? isf_FragNormCoord : clamp(vec2(isf_FragNormCoord[0], isf_FragNormCoord[1]+blurRadiusInPixels),0.0,1.0);
		if (PASSINDEX==4)	{
			texOffsets[3] = (blurRadius==0.0) ? isf_FragNormCoord : clamp(vec2(isf_FragNormCoord[0], isf_FragNormCoord[1]-(2.0*blurRadiusInPixels)),0.0,1.0);
			texOffsets[4] = (blurRadius==0.0) ? isf_FragNormCoord : clamp(vec2(isf_FragNormCoord[0], isf_FragNormCoord[1]+(2.0*blurRadiusInPixels)),0.0,1.0);
		}
	}
	
}
'''


class IsfGlowFast(GlslEffect):
    name = 'isf_glow_fast'
    label = 'Glow Fast'
    description = 'Glow Fast (Stylize).'
    dialect = 'isf'
    GLSL = GLSL
    VS = VS
    stage = 'light'
    scale = 0.35
    params = effect_params({
        "p_intensity": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "Intensity",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "intensity"
            }
        },
        "blur_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 12.0,
            "default": 12.0,
            "x-ui": {
                "label": "Blur Amount",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "blurAmount"
            }
        }
    })
