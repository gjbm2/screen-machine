"""Biomorph: Use mouse to change constant. Internet resources say that "Biomorphs" like this one were discovered by Dr. Clifford A. Pickover reportedly by accident. As far...

Source: https://www.shadertoy.com/view/ltVGWG
Author: vmednis
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: Permission is hereby granted, free of charge, to any person obtaining a copy of
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*

Copyright (c) 2016 Valters Mednis

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

*/

#define ITERATIONS_MAX 100
#define EXIT_NUMBER 10.0
#define ANTIALIAS_AMOUNT 2

//Function that returns a complex number to power of 5
vec2 complexPower5(vec2 c)
{
    vec2 cRes = c;
    for(int i = 1; i < 5; i++)
    {
        //Multiply the result by the original complex number
    	vec2 cResCopy = cRes;
    	cRes.x = (c.x * cResCopy.x) - (c.y * cResCopy.y);
    	cRes.y = (c.x * cResCopy.y) + (c.y * cResCopy.x);
    }
    return cRes;
}

//Returns the color of a biomorph at position coord
vec4 colorBiomorph(vec2 coord, vec2 morphConstant)
{
    //This part is very similar to crude mandlebrot implementations
    vec2 z = coord;
    for(int i = 0; i < ITERATIONS_MAX; i++)
    {
        if((z.x * z.x < EXIT_NUMBER * EXIT_NUMBER) && (z.y * z.y < EXIT_NUMBER * EXIT_NUMBER) && ((z.x * z.x) + (z.y * z.y) < EXIT_NUMBER * EXIT_NUMBER))
        {
        	//z = z^5 + c
        	z = complexPower5(z) + morphConstant;
        }
    }
    
    //Unlike mandelbrot and likes this is not colored according to the number of iterations
    //it took to reach the exit number, but rather the according to the number itself after
    //these iterations
    if((z.x * z.x < EXIT_NUMBER * EXIT_NUMBER) || (z.y * z.y < EXIT_NUMBER * EXIT_NUMBER))
    {
        return vec4(0.0, 0.0, 0.0, 1.0);
    }
    else
    {
        return vec4(1.0, 1.0, 1.0, 1.0);
    }
}

//Simple multisampling-antialising
//Effectively the same as rendering the thing in a larger resolution and then downscaling
vec4 antiAliasedBiomorph(vec2 uv, vec2 constant)
{
    vec4 result = vec4(0.0, 0.0, 0.0, 0.0);
    vec2 sampleStep = 1.0 / iResolution.xy / float(ANTIALIAS_AMOUNT + 1);
    for(int i = 1; i < ANTIALIAS_AMOUNT + 1; i++)
    {
        for(int j = 1; j < ANTIALIAS_AMOUNT + 1; j++)
        {
            vec2 currentPos = uv;
            currentPos.x += sampleStep.x * float(i);
            currentPos.y += sampleStep.y * float(j);
            result += colorBiomorph((currentPos) * 4.0, constant);
        }
    }
    return result / float(ANTIALIAS_AMOUNT * ANTIALIAS_AMOUNT);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 uv = fragCoord.xy / iResolution.y;
    //Position the Biomorph
    if(iResolution.x > iResolution.y)
    {
    	uv.x -= 0.5 * (iResolution.x / iResolution.y) - 0.1777;
   		uv.y -= 0.5;
    }
    else
    {
        uv.y -= 0.5 * (iResolution.y / iResolution.x);
   		uv.x -= 0.3333;
    }
    //Render the Biomorph
    if(iMouse.z < 0.5)
    {
		fragColor = antiAliasedBiomorph(uv, vec2(0.01 * sin(iTime / 4.0) + 0.15, 0.8));
    }
    else
    {
        fragColor = antiAliasedBiomorph(uv, iMouse.xy / iResolution.xy);
    }
}

'''


class StBiomorph(GlslSource):
    name = 'st_biomorph'
    label = 'Biomorph'
    description = 'Use mouse to change constant. Internet resources say that "Biomorphs" like this one were discovered by Dr. Clifford A. Pickover reportedly by accident. As far...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.4)
