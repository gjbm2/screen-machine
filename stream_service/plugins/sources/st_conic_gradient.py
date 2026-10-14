"""Conic gradient: Radial / conic gradient. Forked from https://www.shadertoy.com/view/3l23RK

Source: https://www.shadertoy.com/view/sdlGDN
Author: brunoimbrizi
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Forked from https://www.shadertoy.com/view/3l23RK

// The MIT License
// Copyright © 2019 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Pie:                  https://www.shadertoy.com/view/3l23RK
// Horseshoe:            https://www.shadertoy.com/view/WlSGW1

#define PI 3.1415

float sdHorseshoe( in vec2 p, in vec2 c, in float r, in vec2 w )
{
    p.x = abs(p.x);
    float l = length(p);
    p = mat2(-c.x, c.y, 
              c.y, c.x)*p;
    p = vec2((p.y>0.0)?p.x:l*sign(-c.x),
             (p.x>0.0)?p.y:l );
    p = vec2(p.x,abs(p.y-r))-w;
    return length(max(p,0.0)) + min(0.0,max(p.x,p.y));
}

// c is the sin/cos of the angle. r is the radius
float sdPie( in vec2 p, in vec2 c, in float r )
{
    p.x = abs(p.x);
    float l = length(p) - r;
	float m = length(p - c*clamp(dot(p,c),0.0,r) );
    return max(l,m*sign(c.y*p.x-c.x*p.y));
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // normalized pixel coordinates
    vec2 uv = (fragCoord*2.0-iResolution.xy)/iResolution.y;
    
    // animation
    float falloff = 1.0;
    float t = PI;
    float u = (PI + falloff) * (0.5+0.5*cos(iTime*0.52)) - falloff;
    
    
    // distance
    float d = sdPie(uv,vec2(sin(t),cos(t)), 0.5);
    d = sdHorseshoe(uv,vec2(cos(0.0),sin(0.0)), 0.5, vec2(0.0, 0.2));
    
    // coloring
    vec3 col = vec3(1.0) - sign(d);
    
    float a = 0.0;
	if (uv.y >= 0.0) a =      atan(uv.y,  uv.x);
    if (uv.y <  0.0) a = PI - atan(uv.y, -uv.x);
    
    vec2 angle = vec2(u * 2.0, (u + falloff) * 2.0);
    
    a = smoothstep(angle.x, angle.y, a);
    a = 1.0 - a;

    col = min(col, vec3(a));

	fragColor = vec4(col, 1.0);
}'''


class StConicGradient(GlslSource):
    name = 'st_conic_gradient'
    label = 'Conic gradient'
    description = 'Radial / conic gradient. Forked from https://www.shadertoy.com/view/3l23RK'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
