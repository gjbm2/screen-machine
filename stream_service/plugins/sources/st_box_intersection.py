"""Box   intersection: Intersection of a ray and a box. The ray is transformed to box space, and the returned normal is converted back to ray space upon intersection. If there were...

Source: https://www.shadertoy.com/view/ld23DV
Author: iq
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2014 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.



// Ray-Box intersection, by convertig the ray to the local space of the box.
//
// If this was used to raytace many equally oriented boxes (say you are traversing
// a BVH), then the transformations in line 34 and 35 could be skipped, as well as
// the normal computation in line 50. One over the ray direction is usually accessible
// as well in raytracers, so the division would go away in real world applications.


// List of ray-surface intersectors at https://www.shadertoy.com/playlist/l3dXRf
//
// and http://iquilezles.org/www/articles/intersectors/intersectors.htm


// Other Box functions (http://iquilezles.org/www/articles/boxfunctions/boxfunctions.htm)
//
// Intersection:     https://www.shadertoy.com/view/ld23DV
// Occlusion:        https://www.shadertoy.com/view/4sSXDV
// Occlusion:        https://www.shadertoy.com/view/4djXDy
// Density:          https://www.shadertoy.com/view/Ml3GR8
// Fake soft shadow: https://www.shadertoy.com/view/WslGz4
// Gradient:         https://www.shadertoy.com/view/wlcXD2



// http://iquilezles.org/www/articles/boxfunctions/boxfunctions.htm
vec4 iBox( in vec3 ro, in vec3 rd, in mat4 txx, in mat4 txi, in vec3 rad ) 
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
	
	if( tN > tF || tF < 0.0) return vec4(-1.0);

	vec3 nor = -sign(rdd)*step(t1.yzx,t1.xyz)*step(t1.zxy,t1.xyz);

    // convert to ray space
	
	nor = (txi * vec4(nor,0.0)).xyz;

	return vec4( tN, nor );
}


// http://iquilezles.org/www/articles/boxfunctions/boxfunctions.htm
float sBox( in vec3 ro, in vec3 rd, in mat4 txx, in vec3 rad ) 
{
	vec3 rdd = (txx*vec4(rd,0.0)).xyz;
	vec3 roo = (txx*vec4(ro,1.0)).xyz;

    vec3 m = 1.0/rdd;
    vec3 n = m*roo;
    vec3 k = abs(m)*rad;
	
    vec3 t1 = -n - k;
    vec3 t2 = -n + k;

	float tN = max( max( t1.x, t1.y ), t1.z );
	float tF = min( min( t2.x, t2.y ), t2.z );
	if( tN > tF || tF < 0.0) return -1.0;
	
	return tN;
}


//-----------------------------------------------------------------------------------------

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
/*
mat4 inverse( in mat4 m )
{
	return mat4(
        m[0][0], m[1][0], m[2][0], 0.0,
        m[0][1], m[1][1], m[2][1], 0.0,
        m[0][2], m[1][2], m[2][2], 0.0,
        -dot(m[0].xyz,m[3].xyz),
        -dot(m[1].xyz,m[3].xyz),
        -dot(m[2].xyz,m[3].xyz),
        1.0 );
}*/

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 p = (2.0*fragCoord-iResolution.xy) / iResolution.y;

     // camera movement	
	float an = 0.4*iTime;
	vec3 ro = vec3( 2.5*cos(an), 1.0, 2.5*sin(an) );
    vec3 ta = vec3( 0.0, 0.8, 0.0 );
    // camera matrix
    vec3 ww = normalize( ta - ro );
    vec3 uu = normalize( cross(ww,vec3(0.0,1.0,0.0) ) );
    vec3 vv = normalize( cross(uu,ww));
	// create view ray
	vec3 rd = normalize( p.x*uu + p.y*vv + 2.0*ww );

    // rotate and translate box	
	mat4 rot = rotationAxisAngle( normalize(vec3(1.0,1.0,0.0)), iTime );
	mat4 tra = translate( 0.0, 1.0, 0.0 );
	mat4 txi = tra * rot; 
	mat4 txx = inverse( txi );

    // raytrace
	float tmin = 10000.0;
	vec3  nor = vec3(0.0);
	vec3  pos = vec3(0.0);
	
	// raytrace-plane
	float oid = 0.0;
	float h = (0.0-ro.y)/rd.y;
	if( h>0.0 ) 
	{ 
		tmin = h; 
		nor = vec3(0.0,1.0,0.0); 
		oid = 1.0;
	}

	// raytrace box
	vec3 box = vec3(0.4,0.6,0.8) ;
	vec4 res = iBox( ro, rd, txx, txi, box);
	if( res.x>0.0 && res.x<tmin )
	{
		tmin = res.x; 
		nor = res.yzw;
		oid = 2.0;
	}

    // shading/lighting	
	vec3 col = vec3(0.6,0.75,0.85) - 0.97*rd.y;
    
	if( tmin<100.0 )
	{
		pos = ro + tmin*rd;
		
        // material
		float occ = 1.0;
		vec3  mate = vec3(1.0);
		if( oid<1.5 ) // plane
		{
		    mate = 0.35*texture( iChannel0, 0.25*pos.xz ).xyz;
			occ = 0.2 + 0.8*smoothstep( 0.0, 1.5, length(pos.xz) );
		}			
		else // box
		{
            // recover box space data (we want to do shading in object space)			
		    vec3 opos = (txx*vec4(pos,1.0)).xyz;
			vec3 onor = (txx*vec4(nor,0.0)).xyz;
		    mate = abs(onor.x)*texture( iChannel0, 0.5*opos.yz ).xyz + 
                   abs(onor.y)*texture( iChannel0, 0.5*opos.zx ).xyz + 
                   abs(onor.z)*texture( iChannel0, 0.5*opos.xy ).xyz;
			mate *= 0.35;
            occ = 0.6 + 0.4*nor.y;
		}		
		
        // lighting
        vec3  lig = normalize(vec3(0.8,0.4,-0.6));
        float dif = clamp( dot(nor,lig), 0.0, 1.0 );
        vec3  hal = normalize(lig-rd);
        float sha = step( iBox( pos+0.001*nor, lig, txx, txi, box ).x, 0.0 );
        float amb = 0.6 + 0.4*nor.y;
        float bou = clamp(0.3-0.7*nor.y,0.0,1.0);
        float spe = clamp(dot(nor,hal),0.0,1.0);
        col  = 4.0*vec3(1.00,0.80,0.60)*dif*sha;
        col += 2.0*vec3(0.20,0.30,0.40)*amb;
        col += 2.0*vec3(0.30,0.20,0.10)*bou;
        col *= mate;            
        col += 0.3*pow(spe,8.0)*dif*sha*(0.04+0.96*pow(clamp(dot(lig,hal),0.0,1.0),5.0));
        col = mix( col, vec3(0.6,0.7,0.8), 1.0-exp(-0.001*tmin*tmin) );           
	}
	
    // vignetting        
    col *= 1.0 - 0.1*dot(p,p);

    // gamma
    col = pow( col, vec3(0.45) );
    
	// grading
    col = clamp(col,0.0,1.0);
    col = col*col*(3.0-2.0*col);

	fragColor = vec4( col, 1.0 );
}'''


class StBoxIntersection(GlslSource):
    name = 'st_box_intersection'
    label = 'Box   intersection'
    description = 'Intersection of a ray and a box. The ray is transformed to box space, and the returned normal is converted back to ray space upon intersection. If there were...'
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'noise'}
    params = scene_params({}, render_scale=0.5)
