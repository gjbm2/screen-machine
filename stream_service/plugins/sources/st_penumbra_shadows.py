"""Penumbra Shadows: Improved nurof3n's idea to capture internal penumbra with traditional fake sdf shadows. This method doesn't break the external penumbra, and matches better...

Source: https://www.shadertoy.com/view/WdyXRD
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

// This shader is what I believe the most accurate fake soft shadow implementation
// to date, in that it reproduces correct inner and outer penumbra sizes casting
// a single ray. It matches the ground truth pretty well for different light
// source sizes.
// 
// Code is in line 119


//------------------------------------------------------------------

float sdPlane( vec3 p )
{
	return p.y;
}

float sdBox( vec3 p, vec3 b )
{
    vec3 d = abs(p) - b;
    return min(max(d.x,max(d.y,d.z)),0.0) + length(max(d,0.0));
}

float sdSphere( vec3 p, float r )
{
    return length(p) - r;
}

float sdCylinder(vec3 p, float h, float r)
{
    vec2 q = vec2( length(p.xz)-r, abs(p.y-h*0.5)-h*0.5 );
    return min( max(q.x,q.y),0.0) + length(max(q,0.0));
}

bool shadowBox( in vec3 ro, in vec3 rd, in vec3 cen, in vec3 rad, in float tmax ) 
{
    vec3 m = 1.0/rd;
    vec3 n = m*(ro-cen);
    vec3 k = abs(m)*rad;
    vec3 t1 = -n - k;
    vec3 t2 = -n + k;
	float tN = max( max( t1.x, t1.y ), t1.z );
	float tF = min( min( t2.x, t2.y ), t2.z );
	if( tN > tF || tF < 0.0) return false;
	return tN>0.0 && tN<tmax;
}

bool shadowSphere( in vec3 ro, in vec3 rd, in vec3 cen, in float rad, in float tmax )
{
	vec3 oc = ro - cen;
	float b = dot( oc, rd );
	float c = dot( oc, oc ) - rad*rad;
	float h = b*b - c;
	if( h<0.0 ) return false;
	float t = -b - sqrt( h );
    return t>0.0 && t<tmax;
}

bool shadowCylinder( in vec3 ro, in vec3 rd, in float he, float ra, in float tmax )
{
    float he2 = he*he;
    
    float k2 = 1.0        - rd.y*rd.y;
    float k1 = dot(ro,rd) - ro.y*rd.y;
    float k0 = dot(ro,ro) - ro.y*ro.y - ra*ra;
    
    float h = k1*k1 - k2*k0;
    if( h<0.0 ) return false;
    h = sqrt(h);
    float t = (-k1-h)/k2;

    // body
    float y = ro.y + t*rd.y;
    if( y>0.0 && y<he )
    {
        return t>0.0 && t<tmax;
    }
    
    // caps
    t = ( ((y<0.0) ? 0.0 : he) - ro.y)/rd.y;
    if( abs(k1+k2*t)<h )
    {
        return t>0.0 && t<tmax;
    }

    return false;
}

//------------------------------------------------------------------

float map( in vec3 pos )
{
    vec3 p2 = vec3( mod(pos.x+1.0,3.0)-1.0, pos.yz );
    vec3 p3 = vec3( mod(pos.x+2.0,3.0)-1.0, pos.yz );
    vec3 p4 = vec3( mod(pos.x+3.0,3.0)-1.0, pos.yz );

    float d1 = sdPlane(    pos-vec3(0.0,0.00,0.0) );
    float d2 = sdSphere(   p2-vec3(0.0,0.30,0.0), 0.4 );
    float d3 = sdBox(      p3-vec3(0.0,0.25,0.0), vec3(0.2,0.5,0.2) );
    float d4 = sdCylinder( p4-vec3(0.0,0.0,0.0), 0.8,0.3 );
      
    return min(min(d1,d2),min(d3,d4));
}

//------------------------------------------------------------------
//
// Approximated soft shadows, based on
//
// http://iquilezles.org/www/articles/rmshadows/rmshadows.htm
//
// and
//
// https://www.shadertoy.com/view/tscSRS
//
// and further fixed and improved
//
float apprSoftShadow(vec3 ro, vec3 rd, float mint, float tmax, float w)
{
 	float t = mint;
    float res = 1.0;
    for( int i=0; i<256; i++ )
    {
     	float h = map(ro + t*rd);
        res = min( res, h/(w*t) );
    	t += clamp(h, 0.005, 0.50);
        if( res<-1.0 || t>tmax ) break;
    }
    res = max(res,-1.0); // clamp to [-1,1]

    return 0.25*(1.0+res)*(1.0+res)*(2.0-res); // smoothstep
}


// montecarlo based shadow, for ground truth comparison
float seed; float rand(void) { return fract(sin(seed++)*768.475278); }

float realSoftShadow( in vec3 ro, in vec3 rd, in float tmin, in float tmax, float w )
{
    vec3 uu = normalize(cross(rd,vec3(0,1,0)));
    vec3 vv = normalize(cross(rd,uu));
    
    float tot = 0.0;
    const int num = 32; // cast 32 rays
	for( int j=0; j<num; j++ )
    {
        // uniform distribution on an disk
        float ra = sqrt(rand());
        float an = 6.283185*rand();
        vec3 jrd = rd + w*ra*(uu*cos(an)+vv*sin(an));
        
        // raycast
        float res = 1.0;
        
        for( int i=0; i<7; i++ ) // 7 objects
        {
            int k = i % 3;
            bool sha = false;
                 if(k==0) sha = shadowBox( ro, jrd, vec3(-4.0 + float(i),0.25,0.0), vec3(0.2,0.5,0.2), tmax);
            else if(k==1) sha = shadowSphere(ro, jrd, vec3(-4.0 + float(i),0.3,0.0), 0.4, tmax);
            else          sha = shadowCylinder( ro - vec3(-4.0 + float(i),0.0,0.0), jrd, 0.8, 0.3, tmax);
            
            if( sha ) { res=0.0; break; }
        }
        
        
        tot += res;
    }
    return tot/float(num);
}

vec3 calcNormal( in vec3 pos )
{
    vec2 e = vec2(1.0,-1.0)*0.5773*0.0005;
    return normalize( e.xyy*map( pos + e.xyy ) + 
					  e.yyx*map( pos + e.yyx ) + 
					  e.yxy*map( pos + e.yxy ) + 
					  e.xxx*map( pos + e.xxx ) );
}

float castRay( in vec3 ro, in vec3 rd )
{
    float tmin = 1.0;
    float tmax = 20.0;
   
#if 1
    // bounding volume
    float tp1 = (0.0-ro.y)/rd.y; if( tp1>0.0 ) tmax = min( tmax, tp1 );
    float tp2 = (1.0-ro.y)/rd.y; if( tp2>0.0 ) { if( ro.y>1.0 ) tmin = max( tmin, tp2 );
                                                 else           tmax = min( tmax, tp2 ); }
#endif
    
    float t = tmin;
    for( int i=0; i<128; i++ )
    {
	    float precis = 0.0005*t;
	    float res = map( ro+rd*t );
        if( res<precis || t>tmax ) break;
        t += res;
    }

    if( t>tmax ) t=-1.0;
    return t;
}

float calcAO( in vec3 pos, in vec3 nor )
{
	float occ = 0.0;
    float sca = 1.0;
    for( int i=0; i<5; i++ )
    {
        float h = 0.001 + 0.15*float(i)/4.0;
        float d = map( pos + h*nor );
        occ += (h-d)*sca;
        sca *= 0.95;
    }
    return clamp( 1.0 - 1.5*occ, 0.0, 1.0 );    
}

vec3 render( in vec3 ro, in vec3 rd, in int technique, in float lightSize)
{ 
    vec3  col = vec3(0.0);
    float t = castRay(ro,rd);

    if( t>-0.5 )
    {
        vec3 pos = ro + t*rd;
        vec3 nor = calcNormal( pos );
        
        // material        
		vec3 mate = vec3(0.3);

        // key light
        vec3  lig = normalize( vec3(-0.1, 0.3, 0.6) );
        vec3  hal = normalize( lig-rd );
        
        float sha = (technique==0) ? realSoftShadow(pos, lig, 0.01, 3.0, lightSize ) 
                                   : apprSoftShadow(pos, lig, 0.01, 3.0, lightSize );
        float dif = clamp( dot( nor, lig ), 0.0, 1.0 ) * sha;
        
		float spe = pow(clamp(dot(nor,hal),0.0,1.0),16.0)*
                    dif *
                    (0.04+0.96*pow(clamp(1.0+dot(hal,rd),0.0,1.0),5.0));

		col = mate * 4.0*dif*vec3(1.00,0.70,0.5);
        col +=       9.0*spe*vec3(0.90,0.80,1.0);
        
        // ambient light
        float occ = (pos.y>0.01) ? 1.0 : calcAO( pos, nor );
		float amb = 0.5 + 0.5*nor.y;
        col += mate*amb*occ*vec3(0.05,0.1,0.15);
        
        // fog
        col *= exp( -0.0008*t*t*t );
    }

	return col;
}

mat3 setCamera( in vec3 ro, in vec3 ta, float cr )
{
	vec3 cw = normalize(ta-ro);
	vec3 cp = vec3(sin(cr), cos(cr),0.0);
	vec3 cu = normalize( cross(cw,cp) );
	vec3 cv = normalize( cross(cu,cw) );
    return mat3( cu, cv, cw );
}

#if HW_PERFORMANCE==0
#define AA 1
#else
#define AA 2   // make this 2 or 3 for antialiasing
#endif

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // camera	
    float an = 12.0 - sin(0.1*iTime);
    vec3 ro = vec3( 3.0*cos(0.1*an), 1.0, -3.0*sin(0.1*an) );
    vec3 ta = vec3( 0.0, -0.4, 0.0 );
    
    // camera-to-world transformation
    mat3 ca = setCamera( ro, ta, 0.0 );

    seed = sin(fragCoord.x*114.0)*sin(fragCoord.y*211.1)*sin(iTime);
    
    // animation
    int technique = (fract(iTime/3.0)>0.5) ? 1 : 0;
    float lightSize = 0.05 + 0.04*sin(0.7*iTime);

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

        // ray direction
        vec3 rd = ca * normalize( vec3(p.xy,2.0) );

        // render	
        vec3 col = render( ro, rd, technique, lightSize);

        // gain
        col = 1.8*col/(1.0+dot(col,vec3(0.333)));
        
		// gamma
        col = pow( col, vec3(0.4545) );

        tot += col;
#if AA>1
    }
    tot /= float(AA*AA);
#endif

    
    fragColor = vec4( tot, 1.0 );
}'''


class StPenumbraShadows(GlslSource):
    name = 'st_penumbra_shadows'
    label = 'Penumbra Shadows'
    description = "Improved nurof3n's idea to capture internal penumbra with traditional fake sdf shadows. This method doesn't break the external penumbra, and matches better..."
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.35)
