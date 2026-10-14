"""Cosine   distance 2 D: Exact euclidean distance to a cosine wave of arbitrary amplitude, frequency, phase and offset. It is exact indeed but requires 12 iterations to get full...

Source: https://www.shadertoy.com/view/3t23WG
Author: iq
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2019 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


// Distance to cosine wave of arbitrary amplitude, frequency, phase and offset
// by reducing the problem to the first quadrant of a unit cosine wave, and then
// finding the derivative of the distance squared. The problem reduces to
//
// For y(x) = w·cos(x)
//
// d² (p,x) = (x-p.x)² + (w·cos(x)-p.y)²
// d²'(p,x) = 2[x-p.x - w·sin(x)·(w·cos(x)-p.y)] = 0
//
// I implemented two different root finders. A simple bisection method which uses
// 24 iterations to give full floating point precision, and an 8 iterations
// bisection followed by 4 newtown-raphson method (12 iterations total) that
// provides full floating point precision as well. I am always worried about
// convergence of newtwon-raphson methods, so I give it only as an option.


// List of other 2D distances: https://www.shadertoy.com/playlist/MXdSRf
//
// and www.iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm


#define METHOD 0  // make 1 for newtown-raphson
   


#if METHOD==0
//----------------------------------------------------------------------
// Distance to y(x) = a + b*cos(cx+d)
//----------------------------------------------------------------------
float udCos( in vec2 p, in float a, in float b, in float c, in float d )
{
    // convert all data to a primitive cosine wave
    p = c*(p-vec2(d,a));

    // reduce to principal half cycle
    const float TPI = 6.28318530718;
    p.x = mod( p.x, TPI); if( p.x>(0.5*TPI) ) p.x = TPI - p.x;

    // find zero of derivative (minimize distance)
    float xa = 0.0, xb = TPI;
    for( int i=0; i<24; i++ ) // 24 bit precision
    {
        float x = 0.5*(xa+xb);
        float y = x-p.x+b*c*sin(x)*(p.y-b*c*cos(x));
        if( y<0.0 ) xa = x; else xb = x;
    }
    float x = 0.5*(xa+xb);
    
    // compute distance    
    vec2 q = vec2(x,b*c*cos(x));
    return length(p-q)/c;
}

#else

//----------------------------------------------------------------------
// Distance to y(x) = a + b*cos(cx+d)
//----------------------------------------------------------------------
float udCos( in vec2 p, in float a, in float b, in float c, in float d )
{
    // convert all data to a primitive cosine wave
    p = c*(p-vec2(d,a));
    
    // reduce to principal half cycle
    const float TPI = 6.28318530718;
    p.x = mod( p.x, TPI); if( p.x>(0.5*TPI) ) p.x = TPI - p.x;

    // find zero of derivative (minimize distance)
    float xa = 0.0, xb = TPI;
    for( int i=0; i<8; i++ ) // bisection, 7 bits more or less
    {
        float x = 0.5*(xa+xb);
        float si = sin(x);
        float co = cos(x);
        float y = x-p.x+b*c*si*(p.y-b*c*co);
        if( y<0.0 ) xa = x; else xb = x;
    }
    float x = 0.5*(xa+xb);
    for( int i=0; i<4; i++ ) // newtown-raphson, 28 bits more or less
    {
        float si = sin(x);
        float co = cos(x);
        float  f = x - p.x + b*c*(p.y*si - b*c*si*co);
        float df = 1.0     + b*c*(p.y*co - b*c*(2.0*co*co-1.0));
        x = x - f/df;
    }
    
    // compute distance    
    vec2 q = vec2(x,b*c*cos(x));
    return length(p-q)/c;
}
#endif

void mainImage(out vec4 fragColor, in vec2 fragCoord)
{
	vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
    vec2 m = (2.0*iMouse.xy-iResolution.xy)/iResolution.y;
        
    // animate
    float t = iTime/3.0;
    float ca = 0.0 + 0.4*cos(t*1.1+1.0); // offset    (y position)
    float cb = 0.3 + 0.2*cos(t*1.2+0.0); // amplitude (y scale)
    float cc = 9.0 + 6.0*cos(t*1.3+2.0); // frequency (x scale)
    float cd = 2.0 + 2.0*cos(t*1.0+3.0); // phase     (x position)
    
    // sdf
    float d = udCos( p, ca, cb, cc, cd );
    
    // colorize
    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
	col *= 1.05 - exp(-4.0*abs(d));
	col *= 0.8 + 0.2*cos(110.0*d);
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.015,abs(d)) );
    
    if( iMouse.z>0.001 )
    {
    d = udCos(m,ca, cb, cc, cd );
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, abs(length(p-m)-abs(d))-0.0025));
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, length(p-m)-0.015));
    }
    
    fragColor = vec4(col,1.0);}
'''


class StCosineDistance2D(GlslSource):
    name = 'st_cosine_distance_2_d'
    label = 'Cosine   distance 2 D'
    description = 'Exact euclidean distance to a cosine wave of arbitrary amplitude, frequency, phase and offset. It is exact indeed but requires 12 iterations to get full...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
