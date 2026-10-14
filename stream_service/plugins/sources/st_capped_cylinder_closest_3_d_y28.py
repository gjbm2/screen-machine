"""Capped Cylinder   Closest 3 D Y28: Following Iq's trend. This is forked from a fork of one of his submissions. I've commented my thought process.

Source: https://www.shadertoy.com/view/sllXzs
Author: Yusef28
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2021 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


// Closest point on a sphere. For closest points on other primitives, check
//
//    https://www.shadertoy.com/playlist/wXsSzB



vec3 closestPointToCyl( vec3 p, vec3 cen, float h)
{
    //For sphere we get the normalized vector
    //from center to point, scale it by radius and add that vector
    //to center
    //return cen + rad*normalize(p-cen);
   
    //So for cyl we can do the same thing but with one less dimention
    //Since a cylinder is an extruded circle, whereever we end up 
    //on the missing axis with our p point, we can use that same value
    //for the missing axis for our center
    //otherwise it's the same
    //here cen.z is radius
    
    //If it's capped though then we just need to make sure to take the min
    //of the capped axis height and that component of the point vector.

    //ALSO we need to make sure we have access to all points within the
    //radiu not just on the surface
    
    //the min here makes sure we get all points withint the radius of xy
    //the sign ajusts for using abs, and I used abs so we could get "Min"
    //in the first place,otherwise min woudl be all the negative xy points
    //and that would suck
    vec2 xy = min(abs(cen.xy + cen.z*normalize(p.xy-cen.xy)),
            abs(p.xy))*sign(p.xy);
            
    //this is the z portion (the capped axis)
    //abs p.z is length of that component
    //I clip when it's greater than the defined height "h"
    //then I use sign so we have both sides.
    float z = min(abs(p.z),h)*sign(p.z);
    return vec3(xy,z);
    //then we have
    
    }
    

//------------------------------------------------------------
// https://iquilezles.org/www/articles/distfunctions/distfunctions.htm
float sdSphere( vec3 p, vec3 cen, float rad)
{
    

    return length(p-cen)-rad;
    
}
// https://iquilezles.org/www/articles/distfunctions/distfunctions.htm
float sdCylinder( vec3 p, vec3 cen, float h)
{
    //I've reduced the Cylinder to this
    vec2 d = abs(vec2(length(p.xy),p.z)) - vec2(cen.z,h);
   // return length(max(d,0.));
    
    // and it can be expaned to
    float dx = length(p.xy) - cen.z;
    float dy = abs(p.z)  - h;
    return min(max(dx,dy),0.0) + length(max(vec2(dx,dy),0.));
    
}

// https://iquilezles.org/www/articles/distfunctions/distfunctions.htm
float sdCapsule( vec3 p, vec3 a, vec3 b, float r )
{
	vec3 pa = p-a, ba = b-a;
	float h = clamp( dot(pa,ba)/dot(ba,ba), 0.0, 1.0 );
	return length( pa - ba*h ) - r;
}

//------------------------------------------------------------

vec3 gPoint;

vec2 map( in vec3 pos, bool showCylinder )
{
    const float rad = 0.9;

    // compute closest point to gPoint on the surace of the sphere
    vec3 closestPoint = closestPointToCyl(gPoint, vec3(0.0,0.,0.4),0.5);
    
    // point
    vec2 res = vec2( sdSphere( pos, gPoint, 0.06 ), 1.0 );
    
    // closest point
    {
    float d = sdSphere( pos, closestPoint, 0.06 );
    if( d<res.x ) res = vec2( d, 4.0 );
    }
    
    // box (semi-transparent)    
    //if( ((samp.x+samp.y)&1)==0 )
    
    if( showCylinder )
    {
    float d = sdCylinder( pos, vec3(0.,0.,0.4),0.5);
    if( d<res.x ) res =  vec2( d, 5.0 );
    }

    // segment
    {
    float d = sdCapsule( pos, gPoint, closestPoint, 0.015 );
    if( d<res.x ) res =  vec2( d, 4.0 );
    }
    
    return res;
}

// http://iquilezles.org/www/articles/normalsSDF/normalsSDF.htm
vec3 calcNormal( in vec3 pos, in bool showSphere )
{
    vec2 e = vec2(1.0,-1.0)*0.5773;
    const float eps = 0.0005;
    return normalize( e.xyy*map( pos + e.xyy*eps, showSphere ).x + 
					  e.yyx*map( pos + e.yyx*eps, showSphere ).x + 
					  e.yxy*map( pos + e.yxy*eps, showSphere ).x + 
					  e.xxx*map( pos + e.xxx*eps, showSphere ).x );
}

// http://iquilezles.org/www/articles/rmshadows/rmshadows.htm
float calcSoftShadow(vec3 ro, vec3 rd, bool showSphere )
{
    float res = 1.0;
    const float tmax = 2.0;
    float t = 0.001;
    for( int i=0; i<64; i++ )
    {
     	float h = map(ro + t*rd, showSphere).x;
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

        // make sphere transparent
        bool showCylinder = (texelFetch(iChannel0,samp&7,0).x>0.25);

        // animate camera
        float an = 0.25*time + 6.283185*iMouse.x/iResolution.x;
        vec3 ro = vec3( 2.4*cos(an), 0.7, 2.4*sin(an) );
        vec3 ta = vec3( 0.0, 0.0, 0.0 );

        // camera matrix
        vec3 ww = normalize( ta - ro );
        vec3 uu = normalize( cross(ww,vec3(0.2,1.0,0.0) ) );
        vec3 vv = normalize( cross(uu,ww));

        // animate point
        gPoint = vec3(sin(iTime),sin(iTime*8.)*.2,sin(iTime+2.));

	    // create view ray
        vec3 rd = normalize( p.x*uu + p.y*vv + 1.5*ww );

        // raycast
        const float tmax = 5.0;
        float t = 0.0;
        float m = -1.0;
        for( int i=0; i<256; i++ )
        {
            vec3 pos = ro + t*rd;
            vec2 hm = map(pos,showCylinder);
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
            vec3  nor = calcNormal(pos,showCylinder);

            // color
            vec3  mate = 0.55 + 0.45*cos( m + vec3(1.9,1.5,.5)*2. );
            
            // lighting	
            col = vec3(0.0);
            {
              // key light
              vec3  lig = normalize(vec3(0.3,0.7,0.2));
              float dif = clamp( dot(nor,lig), 0.0, 1.0 );
              if( dif>0.001 ) dif *= calcSoftShadow(pos+nor*0.001,lig,showCylinder);
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


class StCappedCylinderClosest3DY28(GlslSource):
    name = 'st_capped_cylinder_closest_3_d_y28'
    label = 'Capped Cylinder   Closest 3 D Y28'
    description = "Following Iq's trend. This is forked from a fork of one of his submissions. I've commented my thought process."
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'noise'}
    params = scene_params({}, render_scale=0.5)
