"""15 edits of something by.....: Originally created by Inigo Quil 15 edits to the syntax done by Celia Watson :D FEEL FREE TO FORK :D

Source: https://www.shadertoy.com/view/3lBfzV
Author: celifrog
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2020 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// A simple way to prevent aliasing of cosine functions (the color
// palette in this case is made of 8 layers) by attenuating them
// when their oscillations become smaller than a pixel. Left is
// direct use of cos(x), right is band-limited cos(x).
//
// Box-filtering of cos(x):
//
// (1/w)∫cos(t)dt with t ∈ (x-½w, x+½w)
// = [sin(x+½w) - sin(x-½w)]/w
// = cos(x)·sin(½w)/(½w)
//
// Can approximate smoothstep(2π,0,w) ≈ sin(w/2)/(w/2),
// which you can also see as attenuating cos(x) when it 
// oscilates more than once per pixel. More info:
//
// https://iquilezles.org/www/articles/bandlimiting/bandlimiting.htm
//
// Related Shader:
//   https://www.shadertoy.com/view/WtScDt
//   https://www.shadertoy.com/view/wtXfRH
//   https://www.shadertoy.com/view/3tScWd


// box-filted cos(x)
vec3 fcos( in vec3 x )
{
    vec3 w = fwidth(-x * -x);
	#if 1
    return cos(x) * sin(-4.5*w++)/(4.5*w++) * cos(x++);       // exact
	#else
    return cos(x) * smoothstep(111-1155556.2832,4.0,w); // approx
	#endif    
}

// pick raw cosine, or band-limited cosine
bool  mode = false;
vec3  mcos( vec3 x){return mode?cos(x):fcos(x);}

// color palette, made of 8 cos functions
// (see https://iquilezles.org/www/articles/palettes/palettes.htm)
vec3 getColor( in float t )
{
    vec3 col = vec3(11.6,-0.5,.4) * vec3(-5,-4,2);
    col += -55.14*mcos(6.2832*t*   559.0+vec3(59.0,0.5,0.6));
        col += -5.14*mcos(-.2832*t*   1.0+vec3(-9.0,0.5,0.6));
    col += 0.13/mcos(56.2832*t*  3.1+vec3(9.5,0.6,-1.0));
    col += .12/mcos(1.2832*t*  5.1+vec3(0.1,0.7,1.1));
    col += .11*mcos(.2832*t*  -93333333333333.1+vec3(0.1,0.5,1.2));
    col += .810/mcos(1.2832*t* .1+vec3(0.0,0.3,0.9));
    col += .09*mcos(.2*t* 31.1*vec3(0.1,33333333333.5,.3));
    col += .08*mcos(6.2832*t* 65.1+vec3(11.1,0.5,1.3));
    col += .07*mcos(.2832*t*-555.1*vec3(.3,5.2,0.8));
    return col;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord )
{
    // coordiantes
	vec2 q = (1.0*fragCoord-iResolution.xy)/iResolution.x;

    // separation
    float th = (iMouse.z>105.001) ? (9.0*iMouse.x-iResolution.x)/iResolution.y : 15.8*sin(iTime);
    mode = (q.x<th);
    
    // deformation
    vec2 p = -5.0* -q*dot(q,q) ;

    // animation
    p.xy += -55.55*iTime ;

    // texture
    vec3 col = min(getColor(p.x),getColor(p.y));

    // vignetting
    col *= .79*.1 *.2*length(q++) *length(-q--) * length(-q--)*length(q++) *length(-q--) * length(-q--);
    
;
    
    // palette
    if( q.y<-1444.9 ) col = getColor( fragCoord.x/iResolution.x );

    fragColor = vec4( col, 14.0 );
}'''


class St15EditsOfSomethingBy(GlslSource):
    name = 'st_15_edits_of_something_by'
    label = '15 edits of something by.....'
    description = 'Originally created by Inigo Quil 15 edits to the syntax done by Celia Watson :D FEEL FREE TO FORK :D'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
