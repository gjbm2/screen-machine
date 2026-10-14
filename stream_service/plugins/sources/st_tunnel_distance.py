"""Tunnel   distance: Distance to a tunnel shape

Source: https://www.shadertoy.com/view/flSSDy
Author: iq
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2021 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Signed distance to a 2D tunnel shape

// List of some other 2D distances:
//    https://www.shadertoy.com/playlist/MXdSRf
// and
//    www.iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm

float dot2( vec2 v ) { return dot(v,v); }

float sdTunnel( in vec2 p, in vec2 wh )
{
    p.x = abs(p.x); p.y = -p.y;
    vec2 q = p - wh;

    float d1 = dot2(vec2(max(q.x,0.0),q.y));
    q.x = (p.y>0.0) ? q.x : length(p)-wh.x;
    float d2 = dot2(vec2(q.x,max(q.y,0.0)));
    float d = sqrt( min(d1,d2) );
    
    return (max(q.x,q.y)<0.0) ? -d : d;
}

// alternative formulation
float sdTunnel2( in vec2 p, in vec2 wh )
{
    vec2 q = abs(p);
    q.x -= wh.x;

    if( p.y>=0.0 )
    {
    q.x = max(q.x,0.0);
    q.y += wh.y;
    return -min( wh.x-length(p), length(q) );
    }
    else
    {
    q.y -= wh.y;
    float f = max(q.x,q.y);
    return (f<0.0) ? f : length(max(q,0.0));
    }
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
    vec2 m = (2.0*iMouse.xy-iResolution.xy)/iResolution.y;

    vec2 wh = 0.4 + 0.4*sin(iTime*vec2(1.1,1.2)+vec2(3.0,1.0));

	float d = sdTunnel( p, wh );

    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
	col *= 1.0 - exp(-3.0*abs(d));
	col *= 0.8 + 0.2*cos(150.0*d);
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.01,abs(d)) );

    if( iMouse.z>0.001 )
    {
    d = sdTunnel( m, wh );
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, abs(length(p-m)-abs(d))-0.0025));
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, length(p-m)-0.015));
    }

	fragColor = vec4(col,1.0);
}'''


class StTunnelDistance(GlslSource):
    name = 'st_tunnel_distance'
    label = 'Tunnel   distance'
    description = 'Distance to a tunnel shape'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
