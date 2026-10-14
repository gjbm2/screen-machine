"""Square minus Circle   distance: Exact distance to a square minus a circle. This produces correct exteriors everywhere, unlike using SDF operators to subtract a circle from a square.

Source: https://www.shadertoy.com/view/stlXWH
Author: rodolphito
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2021 Rodol Phito
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Signed distance to a square minus a circle

// List of some other 2D distances: https://www.shadertoy.com/playlist/MXdSRf
//
// and www.iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm

float sdSquareMinusCircle( in vec2 p, in float b ) 
{
    p = abs(p); p = (p.y>p.x) ? p.yx : p.xy;
    if (p.y<b*min(p.x,1.0)) return length(p-vec2(1.,b));
    return max(length(max(p-1.,0.0)) + min(p.x-1.,0.0),sqrt(b*b+1.)-length(p));
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 p = 1.5*(2.0*fragCoord.xy-iResolution.xy)/iResolution.y;
    vec2 m = 1.5*(2.0*iMouse.xy-iResolution.xy)/iResolution.y;

    
    // corner radius
    float ra = 0.5*sin(iTime*1.2) + .5;

    
	float d = sdSquareMinusCircle( p, ra );

    
    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
	col *= 1.0 - exp(-3.0*abs(d));
	col *= 0.8 + 0.2*cos(150.0*d);
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.015,abs(d)) );

    if( iMouse.z>0.001 )
    {
    d = sdSquareMinusCircle(m, ra );
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, abs(length(p-m)-abs(d))-0.0025));
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, length(p-m)-0.015));
    }
    
	fragColor = vec4(col,1.0);
}'''


class StSquareMinusCircleDistance(GlslSource):
    name = 'st_square_minus_circle_distance'
    label = 'Square minus Circle   distance'
    description = 'Exact distance to a square minus a circle. This produces correct exteriors everywhere, unlike using SDF operators to subtract a circle from a square.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
