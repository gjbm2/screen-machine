"""Circle   parametrization: Space parametrization of a circle, so you can map things to its exterior (say, other SDFs that you want to stamp). Note how the circles are barely distorted....

Source: https://www.shadertoy.com/view/WldSWX
Author: iq
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2020 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Space parametrization of a circle.
//
// It shows a global coordinate system were things stretch at the
// sides BUT coodinates are global and physical, and also a local
// coordinate system that is a regular grid full of circles).

// List of all shape parametrizations: https://www.shadertoy.com/playlist/XclfRs



float dot2( in vec2 v ) { return dot(v,v); }
float cro(in vec2 a, in vec2 b) { return a.x*b.y-a.y*b.x; }

// x = local dist
// y = local perimeter dist
// z = total local perimeter
// w = global distance (sdf)
vec4 paCircle( in vec2 p, float r, float band ) 
{
    float d = length(p) - r;
    float ra = band*round(d/band);
    float l = (r+ra)*(atan(p.x,p.y)+3.1415927);
    return vec4( d-ra, l, 6.283185*(r+ra), d );
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // normalized pixel coordinates
    vec2 uv = (fragCoord*2.0-iResolution.xy)/iResolution.y;
    
    const float ra = 0.5 ;
    const float bandSize = 0.15;
    
    // distance
    vec4 b = paCircle(uv,ra,bandSize);
    
    // base color
    float d = b.w;
    vec3 col = vec3(1.0,0.68,0.35) + vec3(-0.35,0.15,0.6)*step(d,0.0);
    col *= 1.0 - 0.6*exp(-64.0*abs(d));
    col *= 1.0-smoothstep(0.47,0.50,abs(fract(d/bandSize)-0.5));
	col *= 0.9 + 0.2*smoothstep(0.26,0.24,abs(fract(0.5*d/bandSize+0.25)-0.5));
    col += smoothstep(0.004, 0.002, abs(d));

    
    if( d>-bandSize*0.5 )
    {
   
	vec2 q = b.xy;
    // optional - ensure periodicity, but break physicallity
    q.y *= floor(b.z/bandSize)*(bandSize/b.z);
    
    // animate circles
    q.y -= iTime*0.1;
    
    // draw circles
    vec2 uv = fract(q/bandSize+0.5)-0.5;
    float l = length(uv);
    col *= 0.1 + 0.9*smoothstep(0.01,0.04,abs(l-0.35));
    col *= 0.1 + 0.9*smoothstep(0.10,0.11,l);
    }
        
	fragColor = vec4(col, 1.0);
}'''


class StCircleParametrization(GlslSource):
    name = 'st_circle_parametrization'
    label = 'Circle   parametrization'
    description = 'Space parametrization of a circle, so you can map things to its exterior (say, other SDFs that you want to stamp). Note how the circles are barely distorted....'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
