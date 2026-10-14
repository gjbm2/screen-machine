"""Lemon SDF: for more information see: https://www.desmos.com/calculator/kbjqpiv0qq using iq's raymarching implementation from https://www.shadertoy.com/view/tl23RK

Source: https://www.shadertoy.com/view/3tSSDD
Author: spac
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2019 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

#define TAU 6.28318530718

#define SDF_GRADIENT 1
#define SDF_GRADIENT_PERIOD 0.075
//
// `Lemon` is defined as the self-intersecting region of a spindle torus
//
// `position` defines the centre of the lemon (point of symmetry)
//
// `vertical` defines the axis of rotation
//
// `radius` defines the minor radius of the torus
// `focus` defines the major radius of the torus
//
// `vertical`, `radius`, and `focus` are multiplied by the scale of the lemon
//
struct Lemon
{
    vec3 position;
    vec3 vertical;
    float radius;
    float focus;
};

Lemon lemon = Lemon(
	vec3(0.0, 0.0, 0.0),
    normalize(vec3(0.0, 0.0, 1.0)),
    1.15470053838,
    0.57735026919
);

// `sagitta` defines the shape of the lemon and is between 0 (zero volume) and 1 (sphere)
void makeLemon(in float sagitta, out float radius, out float focus)
{
    radius = (sagitta*sagitta + 1.0) / (2.0*sagitta);
    focus  = radius - sagitta;
}

float sdLemonPerfect(vec3 p, Lemon lemon)
{
    vec3 s = p - lemon.position;
    float t = dot(s, lemon.vertical) / dot(lemon.vertical, lemon.vertical);

    vec3 x = t*lemon.vertical;
    vec3 y = s - x;
    float y2 = dot(y, y);
    if (abs(t) >= 1.0) {
        float l = lemon.focus*(abs(t) - 1.0);
        if (y2 <= l*l) {
            // corner case
            return length(sign(t) * lemon.vertical - s);
        }
    }
    // general case
    return length(lemon.focus*-(y/sqrt(y2)) - s) - lemon.radius;
}

float sdLemon(vec3 p, Lemon lemon)
{
    vec3 s = p - lemon.position;
    float t = dot(s, lemon.vertical) / dot(lemon.vertical, lemon.vertical);

    vec3 y = s - t*lemon.vertical;

    return length(lemon.focus*-normalize(y) - s) - lemon.radius;
}

// ===========================

float intersectPlane(vec3 p, vec3 v, vec3 n, float k)
{
    return (k - dot(p, n)) / dot(v, n);
}

float map( in vec3 pos )
{
    float an = 2.5*(0.5+0.5*sin(iTime*1.1+3.0));
    vec2 c = vec2(sin(an),cos(an));
    //return sdCappedTorus(pos, c, 0.4, 0.1 );
    return sdLemonPerfect(pos, lemon);
}

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
    
#define AA 3

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    float radius, focus;
    //makeLemon(0.6 + sin(0.5*iTime)*0.25, radius, focus);
    //lemon.radius = radius;
    //lemon.focus  = focus;
    
    // camera movement	
	float an = 0.7*iTime;
	vec3 ro = 5.0*normalize(vec3( 1.0*cos(an), 0.5, 1.0*sin(an) ));
    vec3 ta = vec3(0.0);
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
            float dif = clamp( dot(nor,vec3(0.57703)), 0.0, 1.0 );
            float amb = 0.5 + 0.5*dot(nor,vec3(0.0,1.0,0.0));
            col = vec3(0.2,0.3,0.4)*amb + vec3(0.8,0.7,0.5)*dif;
        } else {
            
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


class StLemonSdf(GlslSource):
    name = 'st_lemon_sdf'
    label = 'Lemon SDF'
    description = "for more information see: https://www.desmos.com/calculator/kbjqpiv0qq using iq's raymarching implementation from https://www.shadertoy.com/view/tl23RK"
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
