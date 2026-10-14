"""Arc (circle segment)   2 D SDF: Exact arc (circle segment) SDF.

Source: https://www.shadertoy.com/view/tlSGzG
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

float sdArc (in vec2 p, in float a0, in float a1, in float r )
{
    float a = mod(atan(p.y, p.x), radians(360.));

    float ap = a - a0;
    if (ap < 0.)
        ap += radians(360.);
    float a1p = a1 - a0;
    if (a1p < 0.)
        a1p += radians(360.);

    // is a outside [a0, a1]?
    // https://math.stackexchange.com/questions/1044905/simple-angle-between-two-angles-of-circle
    if (ap >= a1p) {
        	// clamp angle to [a0, a1] by finding the closer one of the two
        	// https://gamedev.stackexchange.com/questions/4467/comparing-angles-and-working-out-the-difference
            float da0 = abs(abs(a0 - a) - radians(180.)); 
            float da1 = abs(abs(a1 - a) - radians(180.)); 
            if (da0 > da1)
                a = a0;
            else
                a = a1;
    }

    vec2 q = vec2(r * cos(a), r * sin(a));
    return length(q - p); 
}

float sdArc2 (in vec2 p, in float a0, in float a1, in float r )
{
    float a = mod(atan(p.y, p.x), radians(360.));

    float ap = a - a0;
    if (ap < 0.)
        ap += radians(360.);
    float a1p = a1 - a0;
    if (a1p < 0.)
        a1p += radians(360.);

    // is a outside [a0, a1]?
    // https://math.stackexchange.com/questions/1044905/simple-angle-between-two-angles-of-circle
    if (ap >= a1p) {
        // snap to the closest of the two endpoints
        vec2 q0 = vec2(r * cos(a0), r * sin(a0));
        vec2 q1 = vec2(r * cos(a1), r * sin(a1));
        return min(length(p - q0), length(p - q1));
    }

    return abs(length(p) - r);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;

	float a0 = mod(1. * iTime, radians(360.));
    float a1 = mod(3. * iTime, radians(360.));

	float d = sdArc2( p, a0, a1, .5 );

    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
	col *= 1.0 - exp(-2.0*abs(d));
	col *= 0.8 + 0.2*cos(140.0*d);
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.02,abs(d)) );

	fragColor = vec4(col,1.0);
}'''


class StArcCircleSegment2DSdf(GlslSource):
    name = 'st_arc_circle_segment_2_d_sdf'
    label = 'Arc (circle segment)   2 D SDF'
    description = 'Exact arc (circle segment) SDF.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
