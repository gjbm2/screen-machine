"""Wedge   distance 2 D: Distance to a wedge. Use the mouse to move the apex.

Source: https://www.shadertoy.com/view/wldXWB
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

// Distance to a wedge.  Use the mouse to move the apex.

// List of some other 2D distances: https://www.shadertoy.com/playlist/MXdSRf
//
// and www.iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm


float dot2( in vec2 v ) { return dot(v,v); }
float cro2( in vec2 a, in vec2 b ) { return a.x*b.y - a.y*b.x; }

float sdWedge( in vec2 q, in vec2 a, in vec2 c, in vec2 b )
{
    q -= c, a -= c, b -= c;

    return 
    // distance
    sqrt(min( dot2(q-a*clamp(dot(q,a)/dot(a,a),0.0,1.0)),
              dot2(q-b*clamp(dot(q,b)/dot(b,b),0.0,1.0)) )) * 
    // sign                                  
    sign(max( cro2(a,q),
              cro2(q,b) ));
}

vec2 cossin( in float r )
{
    return vec2(cos(r),sin(r));
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // normalized pixel coordinates
    vec2 p = (fragCoord*2.0-iResolution.xy)/iResolution.y;
    vec2 m = (2.0*iMouse.xy-iResolution.xy)/iResolution.y;
    
    // animation
    float t = 1.0 + 0.1*iTime;
	vec2 a = cossin(0.0*t) * 0.5;
	vec2 b = cossin(1.0*t) * 0.5;
	vec2 c = cossin(2.0*t) * 0.5;
    
    // Make sure our geometry has the right winding order.
    // This line of code would never exist in reality since the
    // right winding order can be ensured at modeling/editing
    // time - it doesn't need to happen at runtime.
    if( cro2(a-b,c-b)>0.0 ) { vec2 tmp=a;a=c;c=tmp; }
    
    // distance
    float d = sdWedge(p,a,b,c);
    
    // coloring
    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
	col *= 1.0 - exp(-3.0*abs(d));
	col *= 0.8 + 0.2*cos(128.0*abs(d));
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.015,abs(d)) );

    if( iMouse.z>0.001 )
    {
    d = sdWedge(m, a,b,c);
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, abs(length(p-m)-abs(d))-0.0025));
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, length(p-m)-0.015));
    }

	fragColor = vec4(col, 1.0);
}'''


class StWedgeDistance2D(GlslSource):
    name = 'st_wedge_distance_2_d'
    label = 'Wedge   distance 2 D'
    description = 'Distance to a wedge. Use the mouse to move the apex.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
