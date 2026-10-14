"""Circle of circles 2d sdf: asdf

Source: https://www.shadertoy.com/view/WlXXzM
Author: vegardno
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2017 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

float sdCircleCircle( in vec2 p, in int n, in float r0, in float r1 )
{
    float a0 = float(n) / radians(360.);
    float a = round(atan(p.x, p.y) * a0) / a0;
    return length(p - r1 * vec2(sin(a), cos(a))) - r0;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 p = (2.0*fragCoord.xy-iResolution.xy)/iResolution.y;
    p *= 2.0;

    float r0 = .3 + .2 * sin(3. * iTime);
    float r1 = 1. + .4 * sin(1. * iTime);
    
	float d = sdCircleCircle( p, 3 + int(mod(2. * iTime, 9.)), r0, r1 );

    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
	col *= 1.0 - exp(-2.0*abs(d));
	col *= 0.8 + 0.2*cos(80.0*d);
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.02,abs(d)) );
    
	fragColor = vec4(col,1.0);
}'''


class StCircleOfCircles2dSdf(GlslSource):
    name = 'st_circle_of_circles_2d_sdf'
    label = 'Circle of circles 2d sdf'
    description = 'asdf'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
