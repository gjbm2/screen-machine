"""Box   parametrization: Space parametrization of a rounded box, so you can map things to its exterior (say, other SDFs that you want to stamp). Note how the circles are slightly...

Source: https://www.shadertoy.com/view/ttcXWX
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

// Space parametrization of a rounded box. Or could also be called
// "rounded box" coordinates.

// List of all shape parametrizations: https://www.shadertoy.com/playlist/XclfRs


float msign( in float x ) { return (x<0.0)?-1.0:1.0; }

// x = local dist
// y = local perimeter dist
// z = total local perimeter
// w = global distance (sdf)
vec4 paBox( in vec2 p, 
            in vec2 b, in float r, 
            in float s )
{
    vec2 q = abs(p)-b;
        
    float l = b.x+b.y + 1.570796*r;
    
    float k1 = min(max(q.x,q.y),0.0) + length(max(q,0.0))-r;
    float k2 = ((q.x>0.0)?atan(q.y,q.x):1.570796);
    float k3 = 3.0 + 2.0*msign(min(p.x,-p.y)) - msign(p.x);
    float k4 = msign(p.x*p.y);
    float k5 = r*k2+max(-q.x,0.0);
    
    float ra = s*round(k1/s);
    
    float l2 = l + 1.570796*ra;

    return vec4(k1-ra,
                k3*l2+k4*(b.y+((q.y>0.0)?k5+k2*ra:q.y)),
                4.0*l2,
                k1);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // pixel coordinates    
    vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.x;

    // animate segment
    float id = floor((iTime+0.1)/3.0);
    vec2  si = vec2(0.35,0.15) + 0.1*cos( vec2(1,2) + id*vec2(3,1) );
    float ra = 0.15 + 0.05*sin(id*0.2);
    
    // distance and parametrization    
    const float band = 0.1;
    vec4 b = paBox( p, si, ra, band );

    // base color
    float d = b.w;
    vec3 col = vec3(1.0,0.68,0.35) + vec3(-0.35,0.15,0.6)*step(d,0.0);
    col *= 1.0 - 0.6*exp(-64.0*abs(d));
    col *= 1.0-smoothstep(0.47,0.50,abs(fract(d/band)-0.5));
	col *= 0.9 + 0.2*smoothstep(0.26,0.24,abs(fract(0.5*d/band+0.25)-0.5));
    col += smoothstep(0.004, 0.002, abs(d));

    // circles
    if( d>-band*0.5 )
    {
	vec2 q = b.xy;
    q.y *= floor(b.z/band)*(band/b.z);  // optional - ensure periodicity, but break physicallity
    q.y -= iTime*0.1;                   // animate circles
    
    vec2 uv = fract(q/band+0.5)-0.5;    // draw circles
    float l = length(uv);
    col *= 0.1 + 0.9*smoothstep(0.01,0.04,abs(l-0.35));
    col *= 0.1 + 0.9*smoothstep(0.10,0.11,l);
    }
        
	fragColor = vec4(col, 1.0);
}'''


class StBoxParametrization(GlslSource):
    name = 'st_box_parametrization'
    label = 'Box   parametrization'
    description = 'Space parametrization of a rounded box, so you can map things to its exterior (say, other SDFs that you want to stamp). Note how the circles are slightly...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
