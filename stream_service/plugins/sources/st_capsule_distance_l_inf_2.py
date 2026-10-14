"""Capsule   distance L inf 2: Distance to an capsule in the [url=https://en.wikipedia.org/wiki/Lp_space]L-infinity metric space[/url]. [b]This code is VERY innefficient[/url]. Feel free to...

Source: https://www.shadertoy.com/view/ftS3DD
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

// Distance to a capsule in the L-infinity metric space. Compare
// to euclidean: https://www.shadertoy.com/view/3tdSDj
//
// List of some other 2D distances in L-infinity:
//
// https://www.shadertoy.com/playlist/XXccDH

// -------------------------------
// THIS CODE IS VERY INNEFICIENT, RIGHT NOW IT'S DOING A BRUTE
// FORCE COMPUTATION. SOME OTHER DAY I'll GIVE IT A TRY
// -------------------------------


float sdCapsule( in vec2 p, vec2 va, vec2 vb, float rb )
{
    // recenter primitive
    p -= (vb+va)*0.5;
    const float k = sqrt(0.5);
    float l = length(vb-va);
    vec2 u = (vb-va)/l;
    vec2 v = vec2(-u.y,u.x);
    l *= 0.5;
    vec2 a = u*l;
    vec2 w = v*rb;

    // distance to body sides
    float ss = (u.x*u.y>0.0)?1.0:-1.0;
    float de = 1.0/(a.y+ss*a.x);
    vec2 w1 = p-a-w;
    vec2 w2 = p-a+w;
    vec2 q1 = abs(w1+a*clamp(-(w1.y+ss*w1.x)*de, 0.0, 2.0));
    vec2 q2 = abs(w2+a*clamp(-(w2.y+ss*w2.x)*de, 0.0, 2.0));
    float d1 = max(q1.x,q1.y);
    float d2 = max(q2.x,q2.y);
    float d = min(d1,d2);

    // inside circular caps
    vec2 pa = p - a;
    vec2 pb = p + a;
    float da = dot(pa,pa);
    float db = dot(pb,pb);
    if( min(da,db)<rb*rb )
    {
        float s = 1.0; if( db<da ) {pa=pb;s=-1.0;}
        float b1 = 0.5*(pa.x+pa.y);
        float b2 = 0.5*(pa.x-pa.y);
        float c = dot(pa,pa) - rb*rb;
        vec2  t1 = vec2(-b1,b1)+sqrt(b1*b1-c*0.5);
        vec2  t2 = vec2(-b2,b2)+sqrt(b2*b2-c*0.5);
        // 4 solution. Up to 3 can be valid
        if( s*dot(pa+vec2( 1, 1)*t1.x,-u)<0.0 ) d=min(d,t1.x);
        if( s*dot(pa+vec2(-1,-1)*t1.y,-u)<0.0 ) d=min(d,t1.y);
        if( s*dot(pa+vec2( 1,-1)*t2.x,-u)<0.0 ) d=min(d,t2.x);
        if( s*dot(pa+vec2(-1, 1)*t2.y,-u)<0.0 ) d=min(d,t2.y);
        d = -d;
    }
    // outside circular caps
    else 
    {
        vec2  qa = abs(mat2(u.x,-u.y,u.y,u.x)*p) - vec2(l,rb);
        float di = max(qa.x, qa.y);
        // outside circular caps AND outside of body
        if( di>0.0 )
        {
            float dc;
            
            // first cap
            pa = abs(pa);
            if( abs(pa.y-pa.x)<rb )
            {
                float b = 0.5*(pa.x+pa.y);
                float c = dot(pa,pa) - rb*rb;
                dc = b - sqrt(b*b-c*0.5);
            }
            else
            {
                dc = max(pa.x,pa.y)-rb;
            }
            d = min(d,dc);

            // sadly, we still need to test the second cap
            pb = abs(pb);
            if( abs(pb.y-pb.x)<rb )
            {
                float b = 0.5*(pb.x+pb.y);
                float c = dot(pb,pb) - rb*rb;
                dc = b - sqrt(b*b-c*0.5);
            }
            else
            {
                dc = max(pb.x,pb.y)-rb;
            }            
            d = min(d,dc);
        }
        d *= sign(di);
    }
    
    return d;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord)
{
    vec2 p = (2.0*fragCoord - iResolution.xy) / iResolution.y;
    vec2 m = (2.0*iMouse.xy - iResolution.xy) / iResolution.y;
    p *= 1.9;
    m *= 1.9;

    if( iMouse.x<0.01 ) m=sin(-0.17*iTime*vec2(1.1,1.3)+vec2(0,2));

    vec2  v1 = cos( iTime*0.5 + vec2(0.0,1.00) + 0.0 );
	vec2  v2 = cos( iTime*0.5 + vec2(0.0,3.00) + 1.5 );
    float ra = 0.3*(0.5+0.5*cos(iTime*1.1+1.0));
    
    float d = sdCapsule(p, v1, v2, ra);

    vec3 col = vec3(1.0,0.9,1.0) + sign(d)*vec3(-0.3,0.4,0.3);
	col *= 1.0 - exp(-3.0*abs(d));
	col *= 0.8 + 0.2*cos(80.0*d);
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.015,abs(d)) );
    
    d = sdCapsule(m, v1, v2, ra);
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.004, 0.008, abs(max(abs(p.x-m.x), abs(p.y-m.y)) - abs(d))));
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.004, 0.008, length(p-m)-0.015));

	fragColor = vec4(col,1.0);
}'''


class StCapsuleDistanceLInf2(GlslSource):
    name = 'st_capsule_distance_l_inf_2'
    label = 'Capsule   distance L inf 2'
    description = 'Distance to an capsule in the [url=https://en.wikipedia.org/wiki/Lp_space]L-infinity metric space[/url]. [b]This code is VERY innefficient[/url]. Feel free to...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
