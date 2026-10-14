"""Parallelogram   distance 2 D: Distance to a parallelogram, with only two edge checks (ie, total SDF with just two divisions, and a single square root), by using symmetry.

Source: https://www.shadertoy.com/view/7dlGRf
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


// Distance to a parallelogram, I implemented three methods:
// Method 1: computed by two edges, by symmetry, single square root
// Method 2: computed by interior/exterior, optimization of Pentan's idea
// Method 3: computed by zones

#define METHOD 1

// List of some other 2D distances: https://www.shadertoy.com/playlist/MXdSRf
//
// and www.iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm



// signed distance to a 2D parallelogram (width, height, skew)
float sdParallelogram_1( in vec2 p, float wi, float he, float sk )
{
    vec2  e  = vec2(sk,he);
    float e2 = sk*sk + he*he;

    p = (p.y<0.0)?-p:p;
    // horizontal edge
    vec2  w = p - e; w.x -= clamp(w.x,-wi,wi);
    vec2  d = vec2(dot(w,w), -w.y);
    // vertical edge
    float s = p.x*e.y - p.y*e.x;
    p = (s<0.0)?-p:p;
    vec2  v = p - vec2(wi,0); v -= e*clamp(dot(v,e)/e2,-1.0,1.0);
    d = min( d, vec2(dot(v,v), wi*he-abs(s)));
    return sqrt(d.x)*sign(-d.y);
}

float sdParallelogram_2( in vec2 p, float wi, float he, float sk )
{
    vec2  e  = vec2(sk,he);
    float e2 = sk*sk + he*he;

    float da = abs(p.x*e.y-p.y*e.x)-wi*he;
    float db = abs(p.y)-e.y;
    if( max(da,db)<0.0 ) // interior
    {
        return max( da*inversesqrt(e2), db );
    }
    else                 // exterior
    {
       float f = clamp(p.y/e.y,-1.0,1.0);
       float g = clamp(p.x-e.x*f, -wi, wi);
       float h = clamp(((p.x-g)*e.x+p.y*e.y)/e2,-1.0,1.0);
       return length(p-vec2(g+e.x*h,e.y*h));
    }
}

float sdParallelogram_3( in vec2 p, float wi, float he, float sk )
{
    // above
    float db = abs(p.y)-he;
    if( db>0.0 && abs(p.x-sk*sign(p.y))<wi )
        return db;
        
    // inside
    float e2 = sk*sk + he*he;
    float h  = p.x*he - p.y*sk;
    float da = (abs(h)-wi*he)*inversesqrt(e2);
    if( da<0.0 && db<0.0 )
        return max( da, db );

    // sides
    vec2 q = (h<0.0)?-p:p; q.x -= wi;
    float v = abs(q.x*sk+q.y*he);
    if( v<e2 )
        return da;
    
    // exterior
    return sqrt( dot(q,q)+e2-2.0*v );
}

float sdParallelogram( in vec2 p, float wi, float he, float sk )
{
    #if METHOD==1
    return sdParallelogram_1(p,wi,he,sk);
    #endif
    #if METHOD==2
    return sdParallelogram_2(p,wi,he,sk);
    #endif
    #if METHOD==3
    return sdParallelogram_3(p,wi,he,sk);
    #endif
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
    vec2 m = (2.0*iMouse.xy-iResolution.xy)/iResolution.y;

    // animate
    float sk = 0.5*sin(iTime);
        
    //p.x -= sk; // enable to lock base in place

    // distance
	float d = sdParallelogram(p,0.4,0.6,sk);

    // colorize
    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
	col *= 1.0 - exp(-4.0*abs(d));
	col *= 0.8 + 0.2*cos(120.0*d);
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.01,abs(d)) );

    if( iMouse.z>0.001 )
    {
    d = sdParallelogram(m,0.4,0.6,sk);
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, abs(length(p-m)-abs(d))-0.0025));
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, length(p-m)-0.015));
    }
    
    fragColor = vec4(col,1.0);
}'''


class StParallelogramDistance2D(GlslSource):
    name = 'st_parallelogram_distance_2_d'
    label = 'Parallelogram   distance 2 D'
    description = 'Distance to a parallelogram, with only two edge checks (ie, total SDF with just two divisions, and a single square root), by using symmetry.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
