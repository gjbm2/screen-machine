"""Box   closest 3 D: Closest point to a 3D box. Both interior and exterior queries work. See more primitives here: [url]https://www.shadertoy.com/playlist/wXsSzB[/url]

Source: https://www.shadertoy.com/view/NlXXzs
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


// Closest point on a 3D box. For closest points on other primitives, check
//
//    https://www.shadertoy.com/playlist/wXsSzB



// Returns the closest point o, a 3D box
//   p is the point we are at
//   b is the box radius (3 half side lengths)
//   The box is axis aligned and centered at the origin. For a box rotated 
//   by M,you need to transform p and the returned point by inverse(M).
vec3 closestPointToBox( vec3 p, vec3 b )
{
    vec3   d = abs(p) - b;
    float  m = min(0.0,max(d.x,max(d.y,d.z)));
    return p - vec3(d.x>=m?d.x:0.0,
                    d.y>=m?d.y:0.0,
                    d.z>=m?d.z:0.0)*sign(p);
}

// Alternative implementation
vec3 closestPointToBox2( vec3 p, vec3 b )
{
    vec3 d = abs(p) - b;
    vec3 s = sign(p);

    // interior
    vec3 q; float ma;
                 { q=p; q.x=s.x*b.x; ma=d.x; }
    if( d.y>ma ) { q=p; q.y=s.y*b.y; ma=d.y; }
    if( d.z>ma ) { q=p; q.z=s.z*b.z; ma=d.z; }
    if( ma<0.0 ) return q;

    // exterior
    return p - s*max(d,0.0);
}

// If the point is guaranteed to be always outside of the box, you can
// use closestPointToBoxExterior() instead.
vec3 closestPointToBoxExterior( vec3 p, vec3 b )
{
    return p-sign(p)*max(abs(p)-b,0.0);
}

//------------------------------------------------------------

// https://iquilezles.org/www/articles/distfunctions/distfunctions.htm
float sdBox( vec3 p, vec3 b )
{
    vec3 d = abs(p) - b;
    return min(max(d.x,max(d.y,d.z)),0.0) + length(max(d,0.0));
}

// https://iquilezles.org/www/articles/distfunctions/distfunctions.htm
float sdSphere( vec3 p, vec3 cen, float rad )
{
    return length(p-cen)-rad;
}

// https://iquilezles.org/www/articles/distfunctions/distfunctions.htm
float sdCapsule( vec3 p, vec3 a, vec3 b, float r )
{
	vec3 pa = p-a, ba = b-a;
	float h = clamp( dot(pa,ba)/dot(ba,ba), 0.0, 1.0 );
	return length( pa - ba*h ) - r;
}

// https://iquilezles.org/www/articles/distfunctions/distfunctions.htm
float sdBoxFrame( vec3 p, vec3 b, float e )
{
       p = abs(p  )-b;
  vec3 q = abs(p+e)-e;

  return min(min(
      length(max(vec3(p.x,q.y,q.z),0.0))+min(max(p.x,max(q.y,q.z)),0.0),
      length(max(vec3(q.x,p.y,q.z),0.0))+min(max(q.x,max(p.y,q.z)),0.0)),
      length(max(vec3(q.x,q.y,p.z),0.0))+min(max(q.x,max(q.y,p.z)),0.0));
}

//------------------------------------------------------------

vec3 gPoint;

vec2 map( in vec3 pos, bool showSurface )
{
    const vec3 box_rad = vec3(1.1,0.5,0.6);

    // compute closest point to gPoint on the surace of the box
    vec3 closestPoint = closestPointToBox(gPoint, box_rad );
    
    // point
    vec2 res = vec2( sdSphere( pos, gPoint, 0.06 ), 1.0 );
    
    // closest point
    {
    float d = sdSphere( pos, closestPoint, 0.06 );
    if( d<res.x ) res = vec2( d, 4.0 );
    }
    
    // box (semi-transparent)    
    if( showSurface )
    {
    float d = sdBox( pos, box_rad );
    if( d<res.x ) res =  vec2( d, 3.0 );
    }

    // segment
    {
    float d = sdCapsule( pos, gPoint, closestPoint, 0.015 );
    if( d<res.x ) res =  vec2( d, 4.0 );
    }

    // box edges
    {
    float d = sdBoxFrame( pos, box_rad, 0.01 );
    if( d<res.x ) res =  vec2( d, 5.0 );
    }
    
    return res;
}

// http://iquilezles.org/www/articles/normalsSDF/normalsSDF.htm
vec3 calcNormal( in vec3 pos, in bool showSurface )
{
    vec2 e = vec2(1.0,-1.0)*0.5773;
    const float eps = 0.0005;
    return normalize( e.xyy*map( pos + e.xyy*eps, showSurface ).x + 
					  e.yyx*map( pos + e.yyx*eps, showSurface ).x + 
					  e.yxy*map( pos + e.yxy*eps, showSurface ).x + 
					  e.xxx*map( pos + e.xxx*eps, showSurface ).x );
}

// http://iquilezles.org/www/articles/rmshadows/rmshadows.htm
float calcSoftShadow( vec3 ro, vec3 rd, bool showSurface )
{
    float res = 1.0;
    const float tmax = 2.0;
    float t = 0.001;
    for( int i=0; i<64; i++ )
    {
     	float h = map(ro + t*rd, showSurface).x;
        res = min( res, 64.0*h/t );
    	t += clamp(h, 0.01,0.5);
        if( res<-1.0 || t>tmax ) break;
        
    }
    res = max(res,-1.0);
    return 0.25*(1.0+res)*(1.0+res)*(2.0-res); // smoothstep, in [-1,1]
}

#if HW_PERFORMANCE==0
#define AA 1
#else
#define AA 2
#endif

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec3 tot = vec3(0.0);
    #if AA>1
    for( int m=0; m<AA; m++ )
    for( int n=0; n<AA; n++ )
    {
        // pixel coordinates
        vec2 o = vec2(float(m),float(n)) / float(AA) - 0.5;
        vec2 p = (2.0*(fragCoord+o)-iResolution.xy)/iResolution.y;
        // pixel sample
        ivec2 samp = ivec2(fragCoord)*AA + ivec2(m,n);
        // time sample
        float td = 0.5+0.5*sin(fragCoord.x*114.0)*sin(fragCoord.y*211.1);
        float time = iTime - (1.0/60.0)*(td+float(m*AA+n))/float(AA*AA-1);
        #else    
        // pixel coordinates
        vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
        // pixel sample
        ivec2 samp = ivec2(fragCoord);
        // time sample
        float time = iTime;
        #endif

        // animate camera
        float an = 0.25*time + 6.283185*iMouse.x/iResolution.x;
        vec3 ro = vec3( 2.4*cos(an), 0.7, 2.4*sin(an) );
        vec3 ta = vec3( 0.0, -0.15, 0.0 );

        // camera matrix
        vec3 ww = normalize( ta - ro );
        vec3 uu = normalize( cross(ww,vec3(0.2,1.0,0.0) ) );
        vec3 vv = normalize( cross(uu,ww));

        // animate point
        gPoint = -sin(time*0.8*vec3(1.0,1.1,1.2)+vec3(4.0,2.0,1.0));


        // make box transparent
        bool showSurface = ((samp.x+samp.y)&1)==0;
        
	    // create view ray
        vec3 rd = normalize( p.x*uu + p.y*vv + 1.5*ww );

        // raycast
        const float tmax = 5.0;
        float t = 0.0;
        float m = -1.0;
        for( int i=0; i<256; i++ )
        {
            vec3 pos = ro + t*rd;
            vec2 hm = map(pos,showSurface);
            m = hm.y;
            if( hm.x<0.0001 || t>tmax ) break;
            t += hm.x;
        }
    
        // shade background
        vec3 col = vec3(0.05)*(1.0-0.2*length(p));
        
        // shade objects
        if( t<tmax )
        {
            // geometry
            vec3  pos = ro + t*rd;
            vec3  nor = calcNormal(pos,showSurface);

            // color
            vec3  mate = 0.55 + 0.45*cos( m + vec3(0.0,1.0,1.5) );
            
            // lighting	
            col = vec3(0.0);
            {
              // key light
              vec3  lig = normalize(vec3(0.3,0.7,0.2));
              float dif = clamp( dot(nor,lig), 0.0, 1.0 );
              if( dif>0.001 ) dif *= calcSoftShadow(pos+nor*0.001,lig,showSurface);
              col += mate*vec3(1.0,0.9,0.8)*dif;
            }
            {
              // dome light
              float dif = 0.5 + 0.5*nor.y;
              col += mate*vec3(0.2,0.3,0.4)*dif;
            }
        }

        // gamma        
        col = pow( col, vec3(0.4545) );
	    tot += col;
    #if AA>1
    }
    tot /= float(AA*AA);
    #endif

    // cheap dithering
    tot += sin(fragCoord.x*114.0)*sin(fragCoord.y*211.1)/512.0;

	fragColor = vec4( tot, 1.0 );
}'''


class StBoxClosest3D(GlslSource):
    name = 'st_box_closest_3_d'
    label = 'Box   closest 3 D'
    description = 'Closest point to a 3D box. Both interior and exterior queries work. See more primitives here: [url]https://www.shadertoy.com/playlist/wXsSzB[/url]'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
