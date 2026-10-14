"""Plant or Starfish?: demo made for GenArtHackParty4: http://art4.genarthackparty.com/ You can click-drag left/right on the scene to change the viewpoint (ish) Based on Inigo...

Source: https://www.shadertoy.com/view/XsjfDG
Author: rms80
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Derivative of Inigo Quilez's Raymarching - Primitives:
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Derivative of Inigo Quilez's Raymarching - Primitives:
//    https://www.shadertoy.com/view/Xds3zN
// Edits Copyright (c) 2017 Ryan Schmidt
// Edits released under MIT License
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


// Original License follows


// The MIT License
// Copyright © 2013 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
    

// A list of useful distance function to simple primitives, and an example on how to 
// do some interesting boolean operations, repetition and displacement.
//
// More info here: http://www.iquilezles.org/www/articles/distfunctions/distfunctions.htm


#define AA 1   // make this 1 is your machine is too slow

//------------------------------------------------------------------

#define M_PI 3.1415926535897932384626433832795

float sdPlane( vec3 p )
{
	return p.y;
}

float sdSphere( vec3 p, float s )
{
    return length(p)-s;
}

float sdBox( vec3 p, vec3 b )
{
    vec3 d = abs(p) - b;
    return min(max(d.x,max(d.y,d.z)),0.0) + length(max(d,0.0));
}

float sdEllipsoid( in vec3 p, in vec3 r )
{
    return (length( p/r ) - 1.0) * min(min(r.x,r.y),r.z);
}

float udRoundBox( vec3 p, vec3 b, float r )
{
    return length(max(abs(p)-b,0.0))-r;
}

float sdTorus( vec3 p, vec2 t )
{
    return length( vec2(length(p.xz)-t.x,p.y) )-t.y;
}

float sdHexPrism( vec3 p, vec2 h )
{
    vec3 q = abs(p);
#if 0
    return max(q.z-h.y,max((q.x*0.866025+q.y*0.5),q.y)-h.x);
#else
    float d1 = q.z-h.y;
    float d2 = max((q.x*0.866025+q.y*0.5),q.y)-h.x;
    return length(max(vec2(d1,d2),0.0)) + min(max(d1,d2), 0.);
#endif
}

float sdCapsule( vec3 p, vec3 a, vec3 b, float r )
{
	vec3 pa = p-a, ba = b-a;
	float h = clamp( dot(pa,ba)/dot(ba,ba), 0.0, 1.0 );
	return length( pa - ba*h ) - r;
}

float sdTriPrism( vec3 p, vec2 h )
{
    vec3 q = abs(p);
#if 0
    return max(q.z-h.y,max(q.x*0.866025+p.y*0.5,-p.y)-h.x*0.5);
#else
    float d1 = q.z-h.y;
    float d2 = max(q.x*0.866025+p.y*0.5,-p.y)-h.x*0.5;
    return length(max(vec2(d1,d2),0.0)) + min(max(d1,d2), 0.);
#endif
}

float sdCylinder( vec3 p, vec2 h )
{
  vec2 d = abs(vec2(length(p.xz),p.y)) - h;
  return min(max(d.x,d.y),0.0) + length(max(d,0.0));
}

float sdCone( in vec3 p, in vec3 c )
{
    vec2 q = vec2( length(p.xz), p.y );
    float d1 = -q.y-c.z;
    float d2 = max( dot(q,c.xy), q.y);
    return length(max(vec2(d1,d2),0.0)) + min(max(d1,d2), 0.);
}

float sdConeSection( in vec3 p, in float h, in float r1, in float r2 )
{
    float d1 = -p.y - h;
    float q = p.y - h;
    float si = 0.5*(r1-r2)/h;
    float d2 = max( sqrt( dot(p.xz,p.xz)*(1.0-si*si)) + q*si - r2, q );
    return length(max(vec2(d1,d2),0.0)) + min(max(d1,d2), 0.);
}

float sdPryamid4(vec3 p, vec3 h ) // h = { cos a, sin a, height }
{
    // Tetrahedron = Octahedron - Cube
    float box = sdBox( p - vec3(0,-2.0*h.z,0), vec3(2.0*h.z) );
 
    float d = 0.0;
    d = max( d, abs( dot(p, vec3( -h.x, h.y, 0 )) ));
    d = max( d, abs( dot(p, vec3(  h.x, h.y, 0 )) ));
    d = max( d, abs( dot(p, vec3(  0, h.y, h.x )) ));
    d = max( d, abs( dot(p, vec3(  0, h.y,-h.x )) ));
    float octa = d - h.z;
    return max(-box,octa); // Subtraction
 }

float length2( vec2 p )
{
	return sqrt( p.x*p.x + p.y*p.y );
}

float length6( vec2 p )
{
	p = p*p*p; p = p*p;
	return pow( p.x + p.y, 1.0/6.0 );
}

float length8( vec2 p )
{
	p = p*p; p = p*p; p = p*p;
	return pow( p.x + p.y, 1.0/8.0 );
}

float sdTorus82( vec3 p, vec2 t )
{
    vec2 q = vec2(length2(p.xz)-t.x,p.y);
    return length8(q)-t.y;
}

float sdTorus88( vec3 p, vec2 t )
{
    vec2 q = vec2(length8(p.xz)-t.x,p.y);
    return length8(q)-t.y;
}

float sdCylinder6( vec3 p, vec2 h )
{
    return max( length6(p.xz)-h.x, abs(p.y)-h.y );
}

//------------------------------------------------------------------

float opS( float d1, float d2 )
{
    return max(-d2,d1);
}

vec2 opU( vec2 d1, vec2 d2 )
{
	return (d1.x<d2.x) ? d1 : d2;
}

vec3 opRep( vec3 p, vec3 c )
{
    return mod(p,c)-0.5*c;
}

vec3 opTwist( vec3 p )
{
    float  c = cos(10.0*p.y+10.0);
    float  s = sin(10.0*p.y+10.0);
    mat2   m = mat2(c,-s,s,c);
    return vec3(m*p.xz,p.y);
}

float smin( float a, float b, float k )
{
    float h = clamp( 0.5+0.5*(b-a)/k, 0.0, 1.0 );
    return mix( b, a, h ) - k*h*(1.0-h);
}
vec2 smin2( vec2 va, vec2 vb, float k )
{
    float a = va.x; float b = vb.x; 
    float h = clamp( 0.5+0.5*(b-a)/k, 0.0, 1.0 );
    float f = mix( b, a, h ) - k*h*(1.0-h);
    float c = mix( vb.y, va.y, h);
    return vec2(f,c);
}
vec2 opBlend(vec2 d1, vec2 d2, float power) 
{
    return smin2(d1,d2,power);
}


//------------------------------------------------------------------

vec4 quat_identity() {
    return vec4(0,0,0,1);
}


vec4 quat_from_to(vec3 from, vec3 to) {
    vec4 result;
    from = normalize(from);
    to = normalize(to);
    vec3 bisector = normalize(from + to);
    float w = dot(from, bisector);
    if (w != 0.0) {
        result = vec4(cross(from,bisector), w);
    } else {
        if (abs(from.x) >= abs(from.y)) {
            // V1.x or V1.z is the largest magnitude component.
            float invLength = 1.0 / sqrt(from.x * from.x + from.z * from.z);
            result = vec4(-from.z * invLength, 0.0, from.x * invLength, w);
        } else {
            // V1.y or V1.z is the largest magnitude component.
            float invLength = 1.0 / sqrt(from.y * from.y + from.z * from.z);
            result = vec4(0.0, from.z * invLength, -from.y * invLength, w);
        }
    }
    //Normalize();   // aaahhh just to be safe...  
    return result;  
}

vec4 quat_from_axis_angle(vec3 axis, float angle) 
{
    float halfAngle = 0.5 * angle;
    float sn = sin(halfAngle);
    return vec4(sn*axis.x, sn*axis.y, sn*axis.z, cos(halfAngle));
}

vec3 quat_x(vec4 q) {
    float twoY = 2.0 * q.y; float twoZ = 2.0 * q.z;
    float twoWY = twoY * q.w; float twoWZ = twoZ * q.w;
    float twoXY = twoY * q.x; float twoXZ = twoZ * q.x;
    float twoYY = twoY * q.y; float twoZZ = twoZ * q.z;
    return vec3(1.0 - (twoYY + twoZZ), twoXY + twoWZ, twoXZ - twoWY);    
}
vec3 quat_y(vec4 q) {
    float twoX = 2.0 * q.x; float twoY = 2.0 * q.y; float twoZ = 2.0 * q.z;
    float twoWX = twoX * q.w; float twoWZ = twoZ * q.w; float twoXX = twoX * q.x;
    float twoXY = twoY * q.x; float twoYZ = twoZ * q.y; float twoZZ = twoZ * q.z;
    return vec3(twoXY - twoWZ, 1.0 - (twoXX + twoZZ), twoYZ + twoWX);    
}
vec3 quat_z(vec4 q) {
    float twoX = 2.0 * q.x; float twoY = 2.0 * q.y; float twoZ = 2.0 * q.z;
    float twoWX = twoX * q.w; float twoWY = twoY * q.w; float twoXX = twoX * q.x;
    float twoXZ = twoZ * q.x; float twoYY = twoY * q.y; float twoYZ = twoZ * q.y;
    return vec3(twoXZ + twoWY, twoYZ - twoWX, 1.0 - (twoXX + twoYY));
}
vec3 quat_mul(vec4 q, vec3 v) {
    float twoX = 2.0 * q.x; float twoY = 2.0 * q.y; float twoZ = 2.0 * q.z;
    float twoWX = twoX * q.w; float twoWY = twoY * q.w; float twoWZ = twoZ * q.w;
    float twoXX = twoX * q.x; float twoXY = twoY * q.x; float twoXZ = twoZ * q.x;
    float twoYY = twoY * q.y; float twoYZ = twoZ * q.y; float twoZZ = twoZ * q.z;
    return vec3(
        v.x * (1.0 - (twoYY + twoZZ)) + v.y * (twoXY - twoWZ) + v.z * (twoXZ + twoWY),
        v.x * (twoXY + twoWZ) + v.y * (1.0 - (twoXX + twoZZ)) + v.z * (twoYZ - twoWX),
        v.x * (twoXZ - twoWY) + v.y * (twoYZ + twoWX) + v.z * (1.0 - (twoXX + twoYY))); ;    
}

vec4 quat_mul(vec4 a, vec4 b) {
    float w = a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z;
    float x = a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y;
    float y = a.w * b.y + a.y * b.w + a.z * b.x - a.x * b.z;
    float z = a.w * b.z + a.z * b.w + a.x * b.y - a.y * b.x;
    return vec4(x, y, z, w);    
}


struct frame3f {
    vec3 origin;
    vec4 rotation;
};

frame3f make_frame(vec3 o, vec3 z) {
    frame3f f;
    f.origin = o;
    f.rotation = quat_from_to(vec3(0,0,1), z);
    return f;
}
frame3f set_frame(vec3 o, vec4 rot) {
    frame3f f;
    f.origin = o;
    f.rotation = rot;
    return f;
}

vec3 from_frame_p(frame3f f, vec3 p) {
    return quat_mul(f.rotation, p) + f.origin;
}
vec3 from_frame_v(frame3f f, vec3 v) {
    return quat_mul(f.rotation, v);
}


vec3 circlePath(float rad, float height, float revPerS, float time) {
    float a = revPerS * time * 6.28318531;
    return vec3( rad * cos(a), height, rad * sin(a) );
}
vec3 circlePath(frame3f frame, float rad, float revPerS, float time) {
    float a = revPerS * time * 6.28318531;
    float x = rad * cos(a); float y = rad * sin(a);
    return frame.origin + x*quat_x(frame.rotation) + y*quat_y(frame.rotation);
}


vec2 map( in vec3 pos )
{
    float groundMat = 1.0;
    vec2 groundPlane = vec2(sdPlane(pos), groundMat);

    float sphereR = 0.2;
    float pathRMin = 0.0;
    float pathRMax = 0.5;
    float blendmin = 0.7;
    float blendmax = 0.2;

    vec2 shapes = vec2(0,0);
    int shape_i = 0;
    int N = 10;
    for ( int kk = 0; kk < 5; ++kk ) {
        float tk = float(kk)/5.0;
        tk += iTime/20.0;
        vec4 rotAround = quat_from_axis_angle(vec3(0,1,0), tk*2.0*M_PI);
        frame3f arcFrame = set_frame(vec3(0,0,0), rotAround);

        for ( int i = 0; i < 10; ++i ) {
            float t = float(i) / 10.0;

            float cr = 2.0;
            float ca = 2.5*t;
            float cx = cr*cos(ca);
            float cy = cr*sin(ca);
            vec3 ct = vec3(cr,0,0);
            vec2 n = normalize(vec2(-cy,cx));

            float h = float(i) * 2.0 * sphereR;

            float useR = sphereR + 0.05*(1.0+cos(t));

            vec3 arcpos = vec3(cx,cy,0) - ct;
            vec3 arcdir = vec3(n.x, n.y, 0);
            arcpos = from_frame_p(arcFrame, arcpos);
            arcdir = from_frame_v(arcFrame, arcdir);
            frame3f f = make_frame(arcpos, arcdir);

            vec3 spherePos = circlePath(f, mix(pathRMin,pathRMax,t), 0.5, iTime + t );
            float sphereMat = float(i+1) * 10.0;

            vec3 dv = pos - spherePos;
            vec2 sphereF = vec2( sdSphere(dv, useR), sphereMat);
            
            if ( shape_i == 0 ) {
                shapes = sphereF;
            } else {
                shapes = opBlend(sphereF, shapes, mix(blendmin,blendmax,t)); 
                //shapes = opU(sphereF, shapes); 
            }
            shape_i++;

        }
    }


    //vec2 final = opU(groundPlane, shapes);
    vec2 final = shapes;
    //vec2 final = groundPlane;
        
    return final;
}





vec2 castRay( in vec3 ro, in vec3 rd )
{
    float tmin = 3.0;
    float tmax = 15.0;
   
#if 1
    // bounding volume
    float tp1 = (-2.0-ro.y)/rd.y; if( tp1>0.0 ) tmax = min( tmax, tp1 );
    float tp2 = (3.6-ro.y)/rd.y; if( tp2>0.0 ) { if( ro.y>3.6 ) tmin = max( tmin, tp2 );
                                                 else           tmax = min( tmax, tp2 ); }
#endif
    
    float t = tmin;
    float m = -1.0;
    for( int i=0; i<10; i++ )
    {
	    float precis = 0.0005*t;
	    vec2 res = map( ro+rd*t );
        if( res.x<precis || t>tmax ) break;
        t += res.x;
	    m = res.y;
    }

    if( t>tmax ) m=-1.0;
    return vec2( t, m );
}



float softshadow( in vec3 ro, in vec3 rd, in float mint, in float tmax )
{
	float res = 1.0;
    float t = mint;
    for( int i=0; i<16; i++ )
    {
		float h = map( ro + rd*t ).x;
        res = min( res, 8.0*h/t );
        t += clamp( h, 0.02, 0.10 );
        if( h<0.001 || t>tmax ) break;
    }
    return clamp( res, 0.0, 1.0 );
}

vec3 calcNormal( in vec3 pos )
{
    vec2 e = vec2(1.0,-1.0)*0.5773*0.0005;
    return normalize( e.xyy*map( pos + e.xyy ).x + 
					  e.yyx*map( pos + e.yyx ).x + 
					  e.yxy*map( pos + e.yxy ).x + 
					  e.xxx*map( pos + e.xxx ).x );
    /*
	vec3 eps = vec3( 0.0005, 0.0, 0.0 );
	vec3 nor = vec3(
	    map(pos+eps.xyy).x - map(pos-eps.xyy).x,
	    map(pos+eps.yxy).x - map(pos-eps.yxy).x,
	    map(pos+eps.yyx).x - map(pos-eps.yyx).x );
	return normalize(nor);
	*/
}

float calcAO( in vec3 pos, in vec3 nor )
{
	float occ = 0.0;
    float sca = 1.0;
    for( int i=0; i<5; i++ )
    {
        float hr = 0.01 + 0.12*float(i)/4.0;
        vec3 aopos =  nor * hr + pos;
        float dd = map( aopos ).x;
        occ += -(dd-hr)*sca;
        sca *= 0.95;
    }
    return clamp( 1.0 - 3.0*occ, 0.0, 1.0 );    
}

vec3 render( in vec3 ro, in vec3 rd )
{ 
    vec3 col = vec3(0.7, 0.9, 1.0) +rd.y*0.8;
    vec2 res = castRay(ro,rd);
    float t = res.x;
	float m = res.y;
    if( m>-0.5 )
    {
        vec3 pos = ro + t*rd;
        vec3 nor = calcNormal( pos );
        vec3 ref = vec3(0,0,0);
        //ref = reflect( rd, nor );
        
        // material        
		col = 0.45 + 0.35*sin( vec3(0.05,0.08,0.10)*(m-1.0) );
        if( m<1.5 )
        {
            
            float f = mod( floor(5.0*pos.z) + floor(5.0*pos.x), 2.0);
            col = 0.3 + 0.1*f*vec3(1.0);
        }

        // lighitng        
        float occ = 1.0;//calcAO( pos, nor );
		vec3  lig = normalize( vec3(-0.4, 0.7, -0.6) );
		float amb = clamp( 0.5+0.5*nor.y, 0.0, 1.0 );
        float dif = clamp( dot( nor, lig ), 0.0, 1.0 );
        float bac = clamp( dot( nor, normalize(vec3(-lig.x,0.0,-lig.z))), 0.0, 1.0 )*clamp( 1.0-pos.y,0.0,1.0);
        float dom = smoothstep( -0.1, 0.1, ref.y );
        float fre = pow( clamp(1.0+dot(nor,rd),0.0,1.0), 2.0 );
		float spe = pow(clamp( dot( ref, lig ), 0.0, 1.0 ),16.0);
        
        //dif *= softshadow( pos, lig, 0.02, 2.5 );
        //dom *= softshadow( pos, ref, 0.02, 2.5 );

		vec3 lin = vec3(0.0);
        lin += 1.30*dif*vec3(1.00,0.80,0.55);
		lin += 2.00*spe*vec3(1.00,0.90,0.70)*dif;
        lin += 0.40*amb*vec3(0.40,0.60,1.00)*occ;
        lin += 0.50*dom*vec3(0.40,0.60,1.00)*occ;
        lin += 0.50*bac*vec3(0.25,0.25,0.25)*occ;
        lin += 0.25*fre*vec3(1.00,1.00,1.00)*occ;
		col = col*lin;

    	col = mix( col, vec3(0.8,0.9,1.0), 1.0-exp( -0.0002*t*t*t ) );
    }

	return vec3( clamp(col,0.0,1.0) );
}

mat3 setCamera( in vec3 ro, in vec3 ta, float cr )
{
	vec3 cw = normalize(ta-ro);
	vec3 cp = vec3(sin(cr), cos(cr),0.0);
	vec3 cu = normalize( cross(cw,cp) );
	vec3 cv = normalize( cross(cu,cw) );
    return mat3( cu, cv, cw );
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 mo = iMouse.xy/iResolution.xy;
	float time = 15.0 + iTime;

    
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

		// camera	
        float camH = 6.0;
        float camD = 4.5;
        float camTime = 0.0;//time;
        vec3 ro = vec3( -0.5+3.5*cos(0.1*camTime + 6.0*mo.x), camH + 2.0*mo.y, camD + 4.0*sin(0.1*camTime + 6.0*mo.x) );
        vec3 ta = vec3( 0, 1.0, 0.0 );
        // camera-to-world transformation
        mat3 ca = setCamera( ro, ta, 0.0 );
        // ray direction
        vec3 rd = ca * normalize( vec3(p.xy,2.0) );

        // render	
        vec3 col = render( ro, rd );

		// gamma
        col = pow( col, vec3(0.4545) );

        tot += col;
#if AA>1
    }
    tot /= float(AA*AA);
#endif

    
    fragColor = vec4( tot, 1.0 );
}'''


class StPlantOrStarfish(GlslSource):
    name = 'st_plant_or_starfish'
    label = 'Plant or Starfish?'
    description = 'demo made for GenArtHackParty4: http://art4.genarthackparty.com/ You can click-drag left/right on the scene to change the viewpoint (ish) Based on Inigo...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
