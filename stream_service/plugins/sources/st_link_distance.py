"""Link   distance: Euclidean distance to chain link. After elongating a torus and simplifying thing, we get a branch free and compact sdLink() with a minimal number of square...

Source: https://www.shadertoy.com/view/wlXSD7
Author: iq
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2019 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


// Euclidean distance to chain link. After elongating a torus with the technique
// in this article https://www.shadertoy.com/view/Ml3fWj, you can simplify a few
// things, producing a branch free and compact sdLink() with a minimal number of
// square roots and operations.


// List of other 3D SDFs: https://www.shadertoy.com/playlist/43cXRl
//
// and http://iquilezles.org/www/articles/distfunctions/distfunctions.htm


float sdLink( in vec3 p, in float le, in float r1, in float r2 )
{
    vec3 q = vec3( p.x, max(abs(p.y)-le,0.0), p.z );
    return length(vec2(length(q.xy)-r1,q.z)) - r2;
}

float map( in vec3 pos )
{
    // animate
    pos.y += 0.5*iTime;

    // paramteres
    const float le = 0.13, r1 = 0.2, r2 = 0.09;
    
    // make a chain out of sdLink's
    vec3 a = pos; a.y = fract(a.y    )-0.5;
    vec3 b = pos; b.y = fract(b.y+0.5)-0.5;
    
    // evaluate two links
    return min(sdLink(a.xyz,le,r1,r2),
               sdLink(b.zyx,le,r1,r2));
}

/*
// build the chain directly, it saves one of four square roots
// over using sdLinks()
float sdChain( in vec3 pos, in float le, in float r1, in float r2 )
{
    float ya = max(abs(fract(pos.y    )-0.5)-le,0.0);
    float yb = max(abs(fract(pos.y+0.5)-0.5)-le,0.0);

    float la = ya*ya - 2.0*r1*sqrt(pos.x*pos.x+ya*ya);
    float lb = yb*yb - 2.0*r1*sqrt(pos.z*pos.z+yb*yb);
    
    return sqrt(dot(pos.xz,pos.xz) + r1*r1 + min(la,lb)) - r2;
}
*/


// http://iquilezles.org/www/articles/normalsSDF/normalsSDF.htm
vec3 calcNormal( in vec3 pos )
{
    vec2 e = vec2(1.0,-1.0)*0.5773;
    const float eps = 0.0005;
    return normalize( e.xyy*map( pos + e.xyy*eps ) + 
					  e.yyx*map( pos + e.yyx*eps ) + 
					  e.yxy*map( pos + e.yxy*eps ) + 
					  e.xxx*map( pos + e.xxx*eps ) );
}
    
// http://iquilezles.org/www/articles/rmshadows/rmshadows.htm
float calcSoftshadow( in vec3 ro, in vec3 rd, in float mint, in float tmax )
{
    float res = 1.0;
    float t = mint;
    for( int i=0; i<16; i++ )
    {
		float h = map( ro + rd*t );
        res = min( res, 8.0*h/t );
        t += clamp( h, 0.02, 0.10 );
        if( res<0.005 || t>tmax ) break;
    }
    return clamp( res, 0.0, 1.0 );
}

float calcOcclusion( in vec3 pos, in vec3 nor )
{
	float occ = 0.0;
    float sca = 1.0;
    for( int i=0; i<5; i++ )
    {
        float hr = 0.01 + 0.15*float(i)/4.0;
        vec3 aopos =  nor * hr + pos;
        float dd = map( aopos );
        occ += -(dd-hr)*sca;
        sca *= 0.95;
    }
    return clamp( 1.0 - occ*1.5, 0.0, 1.0 );
}


#define AA 3

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
     // camera movement	
	float an = 0.7*iTime;
	vec3 ro = vec3( 1.0*cos(an), 0.2, 1.0*sin(an) );
    vec3 ta = vec3( 0.0, 0.0, 0.0 );
    // camera matrix
    vec3 ww = normalize( ta - ro );
    vec3 uu = normalize( cross(ww,vec3(0.0,1.0,0.0) ) );
    vec3 vv =          ( cross(uu,ww));
    
    // render
    vec3 tot = vec3(0.0);
    
    #if AA>1
    for( int m=0; m<AA; m++ )
    for( int n=0; n<AA; n++ )
    {
        // pixel coordinates
        vec2 o = vec2(float(m),float(n)) / float(AA) - 0.5;
        vec2 p = (-iResolution.xy + 2.0*(fragCoord+o))/iResolution.y;
        #else    
        vec2 p = (-iResolution.xy + 2.0*fragCoord)/iResolution.y;
        #endif

	    // create view ray
        vec3 rd = normalize( p.x*uu + p.y*vv + 1.5*ww );

        // raymarch
        const float tmax = 5.0;
        float t = 0.0;
        for( int i=0; i<256; i++ )
        {
            vec3 pos = ro + t*rd;
            float h = map(pos);
            if( h<0.0001 || t>tmax ) break;
            t += h;
        }
        
    
        // shading/lighting	
        vec3 col = vec3(0.0);
        if( t<tmax )
        {
            vec3 pos = ro + t*rd;
            vec3 nor = calcNormal(pos);
            vec3 lig = normalize(vec3(0.6,0.2,0.4));
            vec3 hal = normalize(lig-rd);
            float dif = clamp( dot(nor,lig), 0.0, 1.0 );
            float occ = calcOcclusion( pos, nor );
            if( dif>0.001 ) dif *= calcSoftshadow( pos, lig, 0.01, 1.0 );
            float spe = pow(clamp(dot(nor,hal),0.0,1.0),16.0)*dif*(0.04+0.96*pow(clamp(1.0-dot(hal,-rd),0.0,1.0),5.0));
            float amb = 0.5 + 0.5*dot(nor,vec3(0.0,1.0,0.0));
            col =  vec3(0.5,1.0,1.2)*amb*occ;
            col += vec3(2.8,2.2,1.8)*dif;
            
            col *= 0.2;
            
            col += vec3(2.8,2.2,1.8)*spe*3.0;
        }

        // gamma        
        col = sqrt( col );
	    tot += col;
    #if AA>1
    }
    tot /= float(AA*AA);
    #endif

	fragColor = vec4( tot, 1.0 );
}'''


class StLinkDistance(GlslSource):
    name = 'st_link_distance'
    label = 'Link   distance'
    description = 'Euclidean distance to chain link. After elongating a torus and simplifying thing, we get a branch free and compact sdLink() with a minimal number of square...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.4)
