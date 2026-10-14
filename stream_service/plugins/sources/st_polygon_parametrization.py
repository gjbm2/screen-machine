"""Polygon   parametrization: Space parametrization of a polygon, so you can map things to its exterior (say, other SDFs that you want to stamp). Note how the circles are barely distorted....

Source: https://www.shadertoy.com/view/wtcSDf
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

// Space parametrization of a polygon. It only works for convex
// polygons, has some problems, and I still need to optimize it.
//
// It shows a global coordinate system were things stretch at the
// sides BUT coodinates are global and physical, and also a local
// coordinate system that is a regular grid full of circles).


// List of all shape parametrizations: https://www.shadertoy.com/playlist/XclfRs


float dot2( in vec2 v ) { return dot(v,v); }
float cro(in vec2 a, in vec2 b) { return a.x*b.y-a.y*b.x; }

// https://www.shadertoy.com/view/wdBXRW
float sdPoly( in vec2 p, vec2 verts[5], in float r ) 
{
    const int num = verts.length();
	float s = 1.0;
    float d = length(p-verts[0]);
    for( int i=0; i<num; i++ )
    {
        vec2 a = verts[i];
        vec2 b = verts[(i+1)%num];
       
        vec2  pa = p-a;
        vec2  ba = b-a;
        float h = clamp(dot(pa,ba)/dot(ba,ba), 0.0, 1.0 );
        d = min(d,length( pa - ba*h ));
        
        bvec3 cond = bvec3( p.y>=a.y, p.y<b.y, ba.x*pa.y>ba.y*pa.x );
        if( all(cond) || all(not(cond)) ) s*=-1.0;  
    }
    
    return s*d-r;
}

float angle( in vec2 a, in vec2 b )
{
    float n = atan(dot(a,b),cro(a,b));
    if( n<0.0 )n+=6.283185;
    return n;
}
    
// x = local dist
// y = local perimeter dist
// z = total local perimeter
// w = global distance (sdf)
vec4 paPoly( in vec2 p, vec2 verts[5], float r, float band ) 
{
    const int num = verts.length();

    float od = sdPoly( p, verts, r );
    
    float ra = band*round(od/band);
    
    float d = length(p-verts[0])-ra;
    float l = 0.0;
    float t = 0.0;
    for( int i=0; i<num; i++ )
    {
        vec2 a = verts[ i       ];
        vec2 b = verts[(i+1)%num];
        vec2 c = verts[(i+2)%num];
            
        vec2  pa1 = p-a; vec2 ba1 = b-a;
        vec2  pa2 = p-b; vec2 ba2 = c-b;
        float h1 = dot(pa1,ba1)/dot(ba1,ba1);
        float h2 = dot(pa2,ba2)/dot(ba2,ba2);
        float tmp = length( pa1 - ba1*clamp(h1,0.0,1.0) ) - (r+ra);
        
        float lba = length(ba1);
        
        if( tmp<d || ((i==num-1) && cro(ba1,pa1)>0.0) )
        {
            d = min(d,tmp);
            if( h1>=0.0 && h1<=1.0 )
            {
                l = t + h1*lba;
            }
            else if( h1>1.0 && h2<0.0)
            {
                l = t+lba;
                l += (r+ra)*angle(ba1,pa2);
            }
        }
        t += lba+(r+ra)*angle(ba1,vec2(-ba2.y,ba2.x));
    }
    
    return vec4(d,l,t,od);
}
    

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // normalized pixel coordinates
    vec2 uv = (fragCoord*2.0-iResolution.xy)/iResolution.y;

    // animate polygon
    float id = floor((iTime+0.1)/3.0);
    vec2 kVerts[5];
    for( int i=0; i<5; i++ )
    {
        kVerts[i] = 0.75*cos( float(i)*vec2(1.1,1.3) + vec2(0,2) + id*11.0 );
    }
    
    const float bandSize = 0.15;
    
    // distance
    vec4 b = paPoly(uv,kVerts,0.15,bandSize);

    
    
    // base color
    float d = b.w;
    vec3 col = vec3(1.0,0.68,0.35) + vec3(-0.35,0.15,0.6)*step(d,0.0);
    col *= 1.0 - 0.6*exp(-64.0*abs(d));
    col *= 1.0-smoothstep(0.47,0.50,abs(fract(d/bandSize)-0.5));
	col *= 0.9 + 0.2*smoothstep(0.26,0.24,abs(fract(0.5*d/bandSize+0.25)-0.5));
    col += 1.0-smoothstep(0.0, 0.005, abs(d)-0.003);

    
    if( d>-bandSize*0.5 )
    {
   
	vec2 q = b.xy;
    // optional - ensure periodicity, but break physicallity
    q.y *= floor(b.z/bandSize)*(bandSize/b.z);
    
    // animate circles
    q.y -= iTime*0.1;
    
    // draw circles
    vec2 uv = fract(q/bandSize+0.5)-0.5;
        
    float l = length(uv);
    col *= 0.1 + 0.9*smoothstep(0.0,0.02,abs(l-0.35)-0.03);
    col *= 0.1 + 0.9*smoothstep(0.0,0.02,l-0.10);
    }
        
	fragColor = vec4(col, 1.0);
}'''


class StPolygonParametrization(GlslSource):
    name = 'st_polygon_parametrization'
    label = 'Polygon   parametrization'
    description = 'Space parametrization of a polygon, so you can map things to its exterior (say, other SDFs that you want to stamp). Note how the circles are barely distorted....'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
