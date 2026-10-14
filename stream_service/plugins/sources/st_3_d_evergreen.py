"""3 D Evergreen: Carve up space to render tree!

Source: https://www.shadertoy.com/view/wtBBzV
Author: logangilmour
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// DERIVED FROM:

// The MIT License
// Copyright © 2013 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// A list of useful distance function to simple primitives. All
// these functions (except for ellipsoid) return an exact
// euclidean distance, meaning they produce a better SDF than
// what you'd get if you were constructing them from boolean
// operations.
//
// More info here:
//
// https://www.iquilezles.org/www/articles/distfunctions/distfunctions.htm

#if HW_PERFORMANCE==0
#define AA 1
#else
#define AA 2   // make this 2 or 3 for antialiasing
#endif

//------------------------------------------------------------------
float dot2( in vec2 v ) { return dot(v,v); }
float dot2( in vec3 v ) { return dot(v,v); }
float ndot( in vec2 a, in vec2 b ) { return a.x*b.x - a.y*b.y; }


float sdSphere( vec3 p, float s )
{
    return length(p)-s;
}

float sdCapsule( vec3 p, vec3 a, vec3 b, float r )
{
	vec3 pa = p-a, ba = b-a;
	float h = clamp( dot(pa,ba)/dot(ba,ba), 0.0, 1.0 );
	return length( pa - ba*h ) - r;
}


float rand(vec2 co){
    return fract(sin(dot(co.xy ,vec2(12.9898,78.233))) * 43758.5453);
}

const float pi = 3.1459;

vec2 perp(vec2 p){
    return vec2(p.y,-p.x);
}

vec2 hash( vec2 p ) // replace this by something better
{
	p = vec2( dot(p,vec2(127.1,311.7)), dot(p,vec2(269.5,183.3)) );
	return -1.0 + 2.0*fract(sin(p)*43758.5453123);
}

float noise( in vec2 p )
{
    const float K1 = 0.366025404; // (sqrt(3)-1)/2;
    const float K2 = 0.211324865; // (3-sqrt(3))/6;

	vec2  i = floor( p + (p.x+p.y)*K1 );
    vec2  a = p - i + (i.x+i.y)*K2;
    float m = step(a.y,a.x); 
    vec2  o = vec2(m,1.0-m);
    vec2  b = a - o + K2;
	vec2  c = a - 1.0 + 2.0*K2;
    vec3  h = max( 0.5-vec3(dot(a,a), dot(b,b), dot(c,c) ), 0.0 );
	vec3  n = h*h*h*h*vec3( dot(a,hash(i+0.0)), dot(b,hash(i+o)), dot(c,hash(i+1.0)));
    return dot( n, vec3(70.0) );
}

vec2 warp(vec2 p, int seed){
     return vec2(noise(p*2.+vec2(seed)),noise(p*2.+vec2(seed+100)))*0.04+vec2(noise(p/2.+vec2(seed+100)),noise(p/2.+vec2(seed+200)))*0.2;
}



float needles(vec3 wpos,vec3 A, vec3 B,float scale){
	vec3 dir = B-A;
    float len = length(dir);
    dir = dir/len;
    vec3 up= vec3(0,1,0);
    vec3 left = cross(up,dir);
    float period = .0007;
    vec3 wsp = wpos-A;
    vec3 nsp = vec3(dot(wsp,left),dot(wsp,dir),dot(wsp,up));
    float rot = atan(nsp.z,nsp.x);
    float phase = (rot+pi)/(pi*2.);
    
    nsp.y = clamp(nsp.y,0.,len);
    float wrap = nsp.y/period+phase*period;
    
    float nearest = max(floor(wrap/0.6)*0.6+pi/4.,0.);
    
    vec3 A2 = A+dir*nearest*period;
	vec3 dir2 = A2+(up*sin(nearest)+left*cos(nearest))*(0.02+scale*2.);
    
    return min(
        sdCapsule(wpos,A,B,scale*0.3)
        ,sdCapsule(wpos,A2,dir2,0.001))
        ;
}

float branch(vec3 wpos, vec3 pos3, vec3 dir3){
    int seed = 0;
    float theta = pi/8.;
    float dotperp = 0.;
    float minp = 1000.;
    vec2 uv = wpos.xz;
    float scale = clamp(0.2-pos3.y*0.2,0.,0.5);

    vec2 pos = pos3.xz;
    vec2 dir = dir3.xz;
	//vec2 pos = vec2(0,-1);
    
    mat2 m = mat2(cos(theta),-sin(theta),sin(theta),cos(theta));
    
    int path = seed;
    for(int i=0; i<4; i++){
		vec2 A = pos;
        vec2 B = pos+dir*scale;
    	vec2 pa = uv-A;
        vec2 ba = B-A;
        
        vec2 per = perp(dir);
        
        
        
        dotperp = dot(per,uv-pos);
        bool side = dotperp>0.;
        path = path*2+int(side);
        
        mat2 ms = m;
        float sgn = float(side)*2.-1.;
        ms[0][1]*=sgn;
        ms[1][0]*=sgn;
        vec2 newdir = ms*dir;
        
        float h = clamp( dot(pa,ba)/dot(ba,ba), 0.0, 1.0 );
        float len = needles(wpos,vec3(A.x,pos3.y,A.y),vec3(B.x,pos3.y,B.y),0.002+scale*0.05);
        minp = min(len,minp);
        
        pos += dir*scale;
        dir = newdir;
        //theta += (rand(vec2(float(path*10),0.))*2.-1.)*0.3;
        scale = scale/1.5;
    }
    return minp;
    
}

float tree(vec3 wpos){
    wpos.y+=1.;
    float period = 0.06;
    
    float rot = atan(wpos.z,wpos.x);
    float phase = (rot+pi)/(pi*2.);
    wpos.y = clamp(wpos.y,-1.3,1.1);
    float wrap = wpos.y/period+phase*period;
    
    float nearest = max(-20.,floor(wrap/1.4)*1.4+pi/4.);
    
    vec3 A = vec3(0,nearest*period,0);

    
    return min(sdCapsule(wpos,vec3(0,-1.2,0),vec3(0,1,0),0.025-wpos.y*0.02),branch(wpos,A, vec3(cos(nearest),0,sin(nearest))));
    
}




//------------------------------------------------------------------

vec2 opU( vec2 d1, vec2 d2 )
{
	return (d1.x<d2.x) ? d1 : d2;
}

//------------------------------------------------------------------

#define ZERO (min(iFrame,0))

//------------------------------------------------------------------

vec2 map( in vec3 pos )
{
    vec2 res = vec2( 1e10, 0.0 );

    {
      res = opU( res, vec2( tree(pos), 1 ) );
              res = opU( res, vec2( tree(pos*0.5-vec3(1,1,1)), 1 ) );

    }
    
    
    return res;
}

// http://iquilezles.org/www/articles/boxfunctions/boxfunctions.htm
vec2 iBox( in vec3 ro, in vec3 rd, in vec3 rad ) 
{
    vec3 m = 1.0/rd;
    vec3 n = m*ro;
    vec3 k = abs(m)*rad;
    vec3 t1 = -n - k;
    vec3 t2 = -n + k;
	return vec2( max( max( t1.x, t1.y ), t1.z ),
	             min( min( t2.x, t2.y ), t2.z ) );
}

vec2 raycast( in vec3 ro, in vec3 rd )
{
    vec2 res = vec2(-1.0,-1.0);

    float tmin = 1.0;
    float tmax = 20.0;


    float t = tmin;
        for( int i=0; i<100 && t<tmax; i++ )
        {
            vec2 h = map( ro+rd*t );
            if( abs(h.x)<(0.0002*t) )
            { 
                res = vec2(t,1); 
                break;
            }
            t += pow(h.x,1.3)*0.3;
        }
    
    return res;
}




vec3 render( in vec3 ro, in vec3 rd, in vec3 rdx, in vec3 rdy )
{ 
    vec2 res = raycast(ro,rd);
    float t = res.x;
	float m = res.y;
    

	return vec3( clamp(m,0.0,1.0) );
}

mat3 setCamera( in vec3 ro, in vec3 ta, float cr )
{
	vec3 cw = normalize(ta-ro);
	vec3 cp = vec3(sin(cr), cos(cr),0.0);
	vec3 cu = normalize( cross(cw,cp) );
	vec3 cv =          ( cross(cu,cw) );
    return mat3( cu, cv, cw );
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 mo = iMouse.xy/iResolution.xy;
	float time = 32.0 + iTime*1.5;

    // camera	
    vec3 ta = vec3( 0.5, -0.5, -0.6 );
    vec3 ro = ta + vec3( 4.5*cos(0.1*time), 1.3, 4.5*sin(0.1*time) );
    // camera-to-world transformation
    mat3 ca = setCamera( ro, ta, 0.0 );

    vec3 tot = vec3(0.0);
#if AA>1
    for( int m=ZERO; m<AA; m++ )
    for( int n=ZERO; n<AA; n++ )
    {
        // pixel coordinates
        vec2 o = vec2(float(m),float(n)) / float(AA) - 0.5;
        vec2 p = (2.0*(fragCoord+o)-iResolution.xy)/iResolution.y;
#else    
        vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
#endif

        // ray direction
        vec3 rd = ca * normalize( vec3(p,2.5) );

         // ray differentials
        vec2 px = (2.0*(fragCoord+vec2(1.0,0.0))-iResolution.xy)/iResolution.y;
        vec2 py = (2.0*(fragCoord+vec2(0.0,1.0))-iResolution.xy)/iResolution.y;
        vec3 rdx = ca * normalize( vec3(px,2.5) );
        vec3 rdy = ca * normalize( vec3(py,2.5) );
        
        // render	
        vec3 col = render( ro, rd, rdx, rdy );

        // gain
        // col = col*3.0/(2.5+col);
        
		// gamma
        col = 1.-pow( col, vec3(0.4545) );

        tot += col;
#if AA>1
    }
    tot /= float(AA*AA);
#endif
    
    fragColor = vec4( tot, 1.0 );
}'''


class St3DEvergreen(GlslSource):
    name = 'st_3_d_evergreen'
    label = '3 D Evergreen'
    description = 'Carve up space to render tree!'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
