"""Arc   distance L inf: Distance to an arc in the [url=https://en.wikipedia.org/wiki/Lp_space]L-infinity metric space[/url].

Source: https://www.shadertoy.com/view/7tSGDD
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

// Distance to an arc of a circle in the L-infinity metric space.
//
// List of some other 2D distances in L-infinity:
//
// https://www.shadertoy.com/playlist/XXccDH

float dist( in vec2 p, in vec2 c )
{
    p = abs(p-c);
    return max(p.x,p.y);
}

float sdArc( in vec2 p, float rb, float w, float an )
{
    vec2 u = vec2(cos(an),sin(an));
    vec2 v = vec2(-u.y,u.x);
    float h = sqrt(rb*rb-w*w);

    // bounding points: arc extremes
    float d = min(dist(p, u*w+h*v),
                  dist(p, u*w-h*v));
    // bounding points: cardinals
    if( -rb*u.x<w ) d = min(d,dist(p,vec2(-rb,0.0)));
    if(  rb*u.x<w ) d = min(d,dist(p,vec2( rb,0.0)));
    if( -rb*u.y<w ) d = min(d,dist(p,vec2(0.0,-rb)));
    if(  rb*u.y<w ) d = min(d,dist(p,vec2(0.0, rb)));
    
    // circular section
    float b1 = 0.5*(p.x+p.y);
    float b2 = 0.5*(p.x-p.y);
    float c = dot(p,p) - rb*rb;
    float h1 = b1*b1-c*0.5;
    float h2 = b2*b2-c*0.5;
    if( h1>0.0 )
    {
        vec2 t = vec2(-b1,b1)+sqrt(h1);
        if( dot(p+vec2( 1, 1)*t.x,u)<w ) d=min(d,abs(t.x));
        if( dot(p+vec2(-1,-1)*t.y,u)<w ) d=min(d,abs(t.y));
    }
    if( h2>0.0 )
    {
        vec2 t = vec2(-b2,b2)+sqrt(h2);
        if( dot(p+vec2( 1,-1)*t.x,u)<w ) d=min(d,abs(t.x));
        if( dot(p+vec2(-1, 1)*t.y,u)<w ) d=min(d,abs(t.y));
    }
    
    return d;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord)
{
    vec2 p = (2.0*fragCoord - iResolution.xy) / iResolution.y;
    vec2 m = (2.0*iMouse.xy - iResolution.xy) / iResolution.y;
    p *= 1.5;
    m *= 1.5;

    if( iMouse.x<0.01 ) m=sin(-0.17*iTime*vec2(1.1,1.3)+vec2(0,1));

    float ra = 0.9;
    float w = 0.5 + 0.35*cos(iTime);
    float an = iTime*0.7;

    
    float d = sdArc(p, ra, w, an);

    vec3 col = vec3(1.0,0.9,1.0) + sign(d)*vec3(-0.3,0.4,0.3);
	col *= 1.0 - exp(-3.0*abs(d));
	col *= 0.8 + 0.2*cos(120.0*d);
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.01,abs(d)) );
    
    d = sdArc(m, ra, w, an);
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.015, abs(max(abs(p.x-m.x), abs(p.y-m.y)) - abs(d))));
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.015, length(p-m)-0.015));

	fragColor = vec4(col,1.0);
}'''


class StArcDistanceLInf(GlslSource):
    name = 'st_arc_distance_l_inf'
    label = 'Arc   distance L inf'
    description = 'Distance to an arc in the [url=https://en.wikipedia.org/wiki/Lp_space]L-infinity metric space[/url].'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
