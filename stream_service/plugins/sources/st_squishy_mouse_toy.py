"""Squishy mouse toy: left click to squish

Source: https://www.shadertoy.com/view/sdK3Dt
Author: dysangel
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2020 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


// Signed distance to a line segment. Tutorial on how to derive
//
// the formula and code: https://www.youtube.com/watch?v=PMltMdi1Wzg

// List of some other 2D distances: https://www.shadertoy.com/playlist/MXdSRf
//
// and www.iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm

   
float udSegment( in vec2 p, in vec2 a, in vec2 b )
{
    vec2 ba = b-a;
    vec2 pa = p-a;
    float h =clamp( dot(pa,ba)/dot(ba,ba), 0.0, 1.0 );
    return length(pa-h*ba);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
    vec2 m = (2.0*iMouse.xy-iResolution.xy)/iResolution.y;
    p *= 1.4;
    m *= 1.4;
    
    vec2 v1 = cos( iTime*0.5 + vec2(0.0,1.00) + 0.0 );
	vec2 v2 = cos( iTime*0.5 + vec2(0.0,3.00) + 1.5 );
    float th = 0.5 * (length(p - m)/2.);
    
	float d = udSegment( p, v1, v2 ) - th;
    
    vec3 col = vec3(1.0) - sign(d)*vec3(0.7*sin(iTime / 2. + (iResolution.x * 0.01)+(iResolution.y * 0.01)),0.4*sin(iTime + (iResolution.x * 0.01)+(iResolution.y * 0.01)),.1);
	col *= 1.0 - exp(-3.0*abs(d));
	col *= 0.8 + 0.2*cos(120.0*d);
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.015,abs(d)) );

    if( iMouse.z>0.001 )
    {
    d = udSegment(m, v1, v2) - th;
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, abs(length(p-m)-abs(d))-0.0025));
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, length(p-m)-0.015));
    }
    
	fragColor = vec4(col,1.0);
}'''


class StSquishyMouseToy(GlslSource):
    name = 'st_squishy_mouse_toy'
    label = 'Squishy mouse toy'
    description = 'left click to squish'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
