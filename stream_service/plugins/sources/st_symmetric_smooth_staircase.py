"""Symmetric Smooth Staircase: This is a fork of iq's [url=https://www.shadertoy.com/view/ttBXRG]Smooth Staircase[/url]. When I saw it I had to try. All I did was substitute the gain...

Source: https://www.shadertoy.com/view/3sGSWW
Author: grinist
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Symmetric Smooth Staircse
// This is a fork of iq's Smooth Staircase (https://www.shadertoy.com/view/ttBXRG). When I saw it I had to try.
// All I did was substitute the gain function with one of my favorite interpolation functions.
// As it is based on 1/x, it has the nice property of being symmetric wrt the diagonal y = x.
// Ie. for y = 1/x, it also holds that x = 1/y; if point (4, 0.25) is on the curve, so is (0.25, 4).
// For a smooth staircase it's perfect, it's as if the stair corners were pinched out at right angles from the straight diagonal.

// See: https://www.shadertoy.com/view/Xd2yRd
// Interpolates from 0 to 1 with slope of k at x=0 and x=1.
// k must be greater than zero.
// k<1: sigmoid, k=1: lerp, k>1: seat
float rxEase2(float x, float k)
{
    k = clamp(k, 0.0001, 10000.0); // clamp optional, if you know your k
    x = 0.5 - x; // re-center at 0
    float s = sign(x);
    x = clamp(abs(x) * 2.0, 0.0, 1.0);
    return 0.5 + 0.5 * s * x / (x * (k - 1.0) - k);
}


// License for the original Smooth Staircase: https://www.shadertoy.com/view/ttBXRG
// The MIT License
// Copyright © 2019 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


// Staircase function y=f(x)
//
// k>1 : starts with vertical step
// k<1 : starts with horizontal step
// [Previous description didn't apply, as the steps are as much horizontal as they are vertical]
//
// The inverse function x=f^-1(x) is just the function itselft
// with parameter 1/k instead of k.
float staircase( in float x, in float k )
{
    float i = floor(x);
    float f = fract(x);

    return i + rxEase2(f, k);
    
    //float a = 0.5*pow(2.0*((f<0.5)?f:1.0-f), k);
    //f = (f<0.5)?a:1.0-a;
    
    //return i+f;
}


void mainImage( out vec4 fragColor, in vec2 fragCoord )
{	
    // coordinates    
    float dx = 3.0/iResolution.y;
    vec2  p = fragCoord.xy*dx;
    
    // animate staircase curve
    float k = pow( 2.0, 8.0*sin(3.1415927*iTime));
    
    // background
    vec3 col = vec3( 0.2 + 0.02*mod(floor(p.x)+floor(p.y),2.0) );

    // draw curve y=f(x)
    float y = staircase( p.x, k );
    col = mix( col, vec3(0.2,0.9,0.7), 1.0-smoothstep(0.0, 2.0*dx, abs(p.y-y) ) );
    
    // draw curve x=f^-1(x)
    float x = staircase( p.y, 1.0/k );
    col = mix( col, vec3(0.2,0.9,0.7), 1.0-smoothstep(0.0, 2.0*dx, abs(p.x-x) ) );
    
    fragColor = vec4( col, 1.0 );
}'''


class StSymmetricSmoothStaircase(GlslSource):
    name = 'st_symmetric_smooth_staircase'
    label = 'Symmetric Smooth Staircase'
    description = "This is a fork of iq's [url=https://www.shadertoy.com/view/ttBXRG]Smooth Staircase[/url]. When I saw it I had to try. All I did was substitute the gain..."
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
