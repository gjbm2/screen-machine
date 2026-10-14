"""5 point star: An antialiased 5 point 2d star. This one is [b]not[/b] an euclidean distance field in its interior nor exterior. Uses reflections instead of domain mod()-ing...

Source: https://www.shadertoy.com/view/lsccR8
Author: iq
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2018 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

float sdfStar5( in vec2 p )
{
    // repeat domain 5x
    const vec2 k1 = vec2(0.809016994375, -0.587785252292); // pi/5
    const vec2 k2 = vec2(-k1.x,k1.y);
    p.x = abs(p.x);
    p -= 2.0*max(dot(k1,p),0.0)*k1;
    p -= 2.0*max(dot(k2,p),0.0)*k2;
    
    // draw triangle
    const vec2 k3 = vec2(0.951056516295,  0.309016994375); // pi/10
    return dot( vec2(abs(p.x)-0.3,p.y), k3);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // coords
    float px = 2.0/iResolution.y;
	vec2 q = (2.0*fragCoord-iResolution.xy)/iResolution.y;

    // rotate
    q = mat2(cos(iTime*0.2),sin(iTime*0.2),-sin(iTime*0.2),cos(iTime*0.2)) * q;

	// star shape    
	float d = sdfStar5( q );       

    // colorize
    vec3 col = vec3(0.4,0.7,0.4) - sign(d)*vec3(0.2,0.1,0.0);
	col *= smoothstep(0.005,0.005+2.0*px,abs(d));
  //col *= smoothstep(0.04,0.04+2.0*px,abs(d+0.1));
	col *= 1.1-0.2*length(q);

    fragColor = vec4( col, 1.0 );
}'''


class St5PointStar(GlslSource):
    name = 'st_5_point_star'
    label = '5 point star'
    description = 'An antialiased 5 point 2d star. This one is [b]not[/b] an euclidean distance field in its interior nor exterior. Uses reflections instead of domain mod()-ing...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
