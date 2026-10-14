"""Five pointed star, polar form: Demonstrating a polar equation that generates a five-pointed star.

Source: https://www.shadertoy.com/view/3ts3DS
Author: tpfto
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2019 J. M.
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Polar equation of a star-shaped decagon (pentagram)
// adapted from Inigo Quilez's shader, https://www.shadertoy.com/view/lsccR8

/*
float sdfStar5( in vec2 p ) // iq's function
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
*/

float polarStar( in vec2 p )
{
    const float pi5 = 0.628318530718; // pi/5
    const float ph2 = 3.2360679775; // 2 * phi
    
    float m2 = mod(atan(p.y, p.x)/pi5 + 1.0, 2.0);
    
    return ph2 * length(p) * cos(pi5 * (m2 - 4.0 * step(1.0, m2) + 1.0)) - 1.0;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // coords
    float px = 2.0/iResolution.y;
	vec2 q = (2.0 * fragCoord - iResolution.xy)/iResolution.y;

    // rotate
    float t = 0.2 * iTime;
    q = mat2(cos(t), -sin(t), sin(t), cos(t)) * q;

	// star shape    
	float d = polarStar( q );       

    // colorize
    vec3 col = mix(vec3(0.6, 0.4, 0.8), vec3(0.2, 0.4, 0.6), smoothstep(-2.0 * px, 2.0 * px, d));
	col *= smoothstep(0.02, 0.02 + 2.0 * px, abs(d));
    
	fragColor = vec4( col, 1.0 );
}'''


class StFivePointedStarPolarForm(GlslSource):
    name = 'st_five_pointed_star_polar_form'
    label = 'Five pointed star, polar form'
    description = 'Demonstrating a polar equation that generates a five-pointed star.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
