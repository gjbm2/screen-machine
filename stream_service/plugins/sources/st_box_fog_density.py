"""Box   fog density: Analytically integrating quadratically decaying participating media within a box. No raymarching involved.

Source: https://www.shadertoy.com/view/Ml3GR8
Author: iq
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2016 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.



// Analytically integrating quadratically decaying participating media within a box. 
//
// Similar to https://www.shadertoy.com/view/XljGDy
//
// Density is of the form
//
// d(x,y,z) = [1-(x/rx)^2] * [1-(y/ry)^2] * [1-(z/rz)^2];
//
// this can be analytically integrable (it's a degree 6 polynomial)


// Other Box functions (http://iquilezles.org/www/articles/boxfunctions/boxfunctions.htm)
//
// Intersection:     https://www.shadertoy.com/view/ld23DV
// Occlusion:        https://www.shadertoy.com/view/4sSXDV
// Occlusion:        https://www.shadertoy.com/view/4djXDy
// Density:          https://www.shadertoy.com/view/Ml3GR8
// Fake soft shadow: https://www.shadertoy.com/view/WslGz4
// Gradient:         https://www.shadertoy.com/view/wlcXD2



//-------------------------------------------------------------------------------------------

vec2 iBox( in vec3 ro, in vec3 rd, in mat4 txx, in mat4 txi, in vec3 rad, out vec3 resNor ) 
{
    // convert from ray to box space
	vec3 rdd = (txx*vec4(rd,0.0)).xyz;
	vec3 roo = (txx*vec4(ro,1.0)).xyz;

	// ray-box intersection in box space
    vec3 m = 1.0/rdd;
    vec3 n = m*roo;
    vec3 k = abs(m)*rad;
	
    vec3 t1 = -n - k;
    vec3 t2 = -n + k;

	float tN = max( max( t1.x, t1.y ), t1.z );
	float tF = min( min( t2.x, t2.y ), t2.z );
	
	if( tN > tF || tF < 0.0) return vec2(-1.0);

	vec3 nor = -sign(rdd)*step(t1.yzx,t1.xyz)*step(t1.zxy,t1.xyz);

    // convert to ray space
	
	resNor = (txi * vec4(nor,0.0)).xyz;

	return vec2( tN, tF );
}

mat4 rotationAxisAngle( vec3 v, float angle )
{
    float s = sin( angle );
    float c = cos( angle );
    float ic = 1.0 - c;

    return mat4( v.x*v.x*ic + c,     v.y*v.x*ic - s*v.z, v.z*v.x*ic + s*v.y, 0.0,
                 v.x*v.y*ic + s*v.z, v.y*v.y*ic + c,     v.z*v.y*ic - s*v.x, 0.0,
                 v.x*v.z*ic - s*v.y, v.y*v.z*ic + s*v.x, v.z*v.z*ic + c,     0.0,
			     0.0,                0.0,                0.0,                1.0 );
}

mat4 translate( float x, float y, float z )
{
    return mat4( 1.0, 0.0, 0.0, 0.0,
				 0.0, 1.0, 0.0, 0.0,
				 0.0, 0.0, 1.0, 0.0,
				 x,   y,   z,   1.0 );
}

//======================================================================================

float boxDensity( vec3 wro, vec3 wrd,   // ray origin, ray direction
                  mat4 txx, vec3 r,     // box center and orientation, box radius
                  float dbuffer )       // depth buffer
{
    vec3 d = (txx*vec4(wrd,0.0)).xyz;
	vec3 o = (txx*vec4(wro,1.0)).xyz;

	// ray-box intersection in box space
    vec3 m = 1.0/d;
    vec3 n = m*o;
    vec3 k = abs(m)*r;
    vec3 ta = -n - k;
    vec3 tb = -n + k;
	float tN = max( max( ta.x, ta.y ), ta.z );
	float tF = min( min( tb.x, tb.y ), tb.z );
	if( tN > tF || tF < 0.0) return 0.0;

    // not visible (behind camera or behind dbuffer)
    if( tF<0.0 || tN>dbuffer ) return 0.0;

    // clip integration segment from camera to dbuffer
    tN = max( tN, 0.0 );
    tF = min( tF, dbuffer );
    
    // move ray to the intersection point
    o += tN*d; tF=tF-tN; tN=0.0;

    // density calculation. density is of the form
    //
    // d(x,y,z) = [1-(x/rx)^2] * [1-(y/ry)^2] * [1-(z/rz)^2];
    //
    // this can be analytically integrable (it's a degree 6 polynomial):
    
    vec3 a = 1.0 -     (o*o)/(r*r);
    vec3 b =     - 2.0*(o*d)/(r*r);
    vec3 c =     -     (d*d)/(r*r);
    
    float t1 = tF;
    float t2 = t1*t1;
    float t3 = t2*t1;
    float t4 = t2*t2;
    float t5 = t2*t3;
    float t6 = t3*t3;
    float t7 = t3*t4;

    float f = (t1/1.0) *(a.x*a.y*a.z) + 
              (t2/2.0) *(a.x*a.y*b.z + a.x*b.y*a.z + b.x*a.y*a.z) + 
              (t3/3.0) *(a.x*a.y*c.z + a.x*b.y*b.z + a.x*c.y*a.z + b.x*a.y*b.z + b.x*b.y*a.z + c.x*a.y*a.z) +
              (t4/4.0) *(a.x*b.y*c.z + a.x*c.y*b.z + b.x*a.y*c.z + b.x*b.y*b.z + b.x*c.y*a.z + c.x*a.y*b.z + c.x*b.y*a.z) + 
              (t5/5.0) *(a.x*c.y*c.z + b.x*b.y*c.z + b.x*c.y*b.z + c.x*a.y*c.z + c.x*b.y*b.z + c.x*c.y*a.z) + 
              (t6/6.0) *(b.x*c.y*c.z + c.x*b.y*c.z + c.x*c.y*b.z) + 
              (t7/7.0) *(c.x*c.y*c.z);
      
    return f;
    
}


float plnIntersect( in vec3 ro, in vec3 rd, vec4 pln )
{
    return (pln.w - dot(ro,pln.xyz))/dot(rd,pln.xyz);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 p = (2.0*fragCoord.xy-iResolution.xy) / iResolution.y;
    
	vec3 ro = vec3(0.0, 0.2, 3.0 );
	vec3 rd = normalize( vec3(p,-3.0) );
	
    // box
    vec3 cen = cos( iTime*vec3(1.0,1.1,1.3) + vec3(2.0,1.5,1.5) + 0.0 )*vec3(1.2,0.3,0.7) + vec3(0.0,0.2,0.5);
    vec3 rad = vec3(0.4,0.6,0.8);
    // planes
    vec4 pl1 = vec4(  0.0, 1.0, 0.0, 0.0 );
    vec4 pl2 = vec4(  1.0, 0.0, 0.0, 1.0 );
    vec4 pl3 = vec4( -1.0, 0.0, 0.0, 1.0 );
    vec4 pl4 = vec4(  0.0, 0.0,-1.0, 1.0 );
    
    float th = (-1.0+2.0*smoothstep( 0.8, 0.9, sin( iTime*1.0 )));
    th *= iResolution.x/iResolution.y;
    //th = (2.0*iMouse.x-iResolution.x) / iResolution.y;
    
    vec3 lig = normalize( vec3(0.6,0.3,0.8) );


	mat4 rot = rotationAxisAngle( normalize(vec3(1.0,1.0,0.0)), iTime );
	mat4 tra = translate( cen.x, cen.y, cen.z );
	mat4 txi = tra * rot; 
	mat4 txx = inverse( txi );
        
    //float t1 = sphIntersect( ro, rd, sph );
    vec3 bnor;
    vec2 br = iBox( ro, rd, txx, txi, rad, bnor ) ;
    float t1 = br.x;
    
    float t2 = plnIntersect( ro, rd, pl1 );
    float t3 = plnIntersect( ro, rd, pl2 );
    float t4 = plnIntersect( ro, rd, pl3 );
    float t5 = plnIntersect( ro, rd, pl4 );
    
    float tmin = 1000.0;
    vec4  omin = vec4(0.0);
    if( t2>0.0 && t2<tmin ) { tmin=t2; omin=pl1; }
    if( t3>0.0 && t3<tmin ) { tmin=t3; omin=pl2; }
    if( t4>0.0 && t4<tmin ) { tmin=t4; omin=pl3; }
    if( t5>0.0 && t5<tmin ) { tmin=t5; omin=pl4; }

    vec3 col = vec3(0.0);
    
    if( tmin<999.0 )
    {    
        vec3 pos = ro + tmin*rd;

        col = vec3(0.1,0.15,0.2);
        col *= 0.8 + 0.4*dot(omin.xyz,lig);
        
        vec3 w = abs(omin.xyz);
        col = (texture( iChannel0, 0.5*pos.zx ).xyz*w.y+
               texture( iChannel0, 0.5*pos.xy ).xyz*w.z+
               texture( iChannel0, 0.5*pos.yz ).xyz*w.x)/(w.x+w.y+w.z);
        col *= 0.3;
        float occ = 1.0;
        occ *= smoothstep( 0.0, 0.5, length( pos.xy-vec2( 1.0, 0.0)));
        occ *= smoothstep( 0.0, 0.5, length( pos.xy-vec2(-1.0, 0.0)));
        occ *= smoothstep( 0.0, 0.5, length( pos.yz-vec2( 0.0,-1.0)));
        occ *= smoothstep( 0.0, 0.5, length( pos.xz-vec2( 1.0,-1.0)));
        occ *= smoothstep( 0.0, 0.5, length( pos.xz-vec2(-1.0,-1.0)));
        col *= vec3(0.4,0.3,0.2) + vec3(0.6,0.7,0.8)*occ;
        
        
    }

    if( p.x<th && t1>0.0 && t1<tmin )
    {
        vec3 pos = ro + t1*rd;
        
        col = vec3(0.3);
                
        float occ = 1.0;
        occ = 0.5 + 0.5*bnor.y;
        occ *= clamp( ( pos.x+1.0)*3.0, 0.0, 1.0 );
        occ *= clamp( (-pos.x+1.0)*3.0, 0.0, 1.0 );
        occ *= clamp( ( pos.y-0.0)*3.0, 0.0, 1.0 );
        occ *= clamp( ( pos.z+1.0)*3.0, 0.0, 1.0 );
        col *= 0.5 + 0.5*occ;
    }

   //col *= 0.0;
   if( p.x>th )
    {
        float h = boxDensity(ro, rd, txx, rad, tmin );
        if( h>0.0 )
        {
            col += h;
            //col += smoothstep( 0.1,1.2,h);
            //col = mix( col, vec3(1.0,1.0,1.0), clamp(h,0.0,1.0) );
            //if( h>1.0 ) col=vec3(1.0,0.0,0.0);
        }
    }
    
    col = sqrt( col );
    
    col *= smoothstep( 0.010,0.011,abs(p.x-th));
    
    fragColor = vec4( col, 1.0 );
}'''


class StBoxFogDensity(GlslSource):
    name = 'st_box_fog_density'
    label = 'Box   fog density'
    description = 'Analytically integrating quadratically decaying participating media within a box. No raymarching involved.'
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'noise'}
    params = scene_params({}, render_scale=0.5)
