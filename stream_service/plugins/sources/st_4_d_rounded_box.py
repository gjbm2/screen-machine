"""4 D Rounded Box: I raymarched a 3D slice of a 4D box. The 3D slice (plane) that cuts the 4D box is animated over time, and the cube itself is rotating in 4D space. Note this is...

Source: https://www.shadertoy.com/view/WtB3Wt
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

// I raymarched a 3D slice of a 4D rounded box. The 3D slice (plane) that
// cuts the 4D box is animated over time, and the cube itself is rotating
// in 4D space. Note this is NOT 4D raymarching, it is 3D raymarching (of
// a 3D slice of a 4D world).


#if HW_PERFORMANCE==0
#define AA 1
#else
#define AA 2  // Set AA to 1 if your machine is too slow
#endif


float sdBox( in vec4 p, in vec4 b )
{
    vec4 d = abs(p) - b;
    return min( max(max(d.x,d.y),max(d.z,d.w)),0.0) + length(max(d,0.0));
}

mat4x4 q2m( in vec4 q )
{
    return mat4x4( q.x, -q.y, -q.z, -q.w,
                   q.y,  q.x, -q.w,  q.z,
                   q.z,  q.w,  q.x, -q.y,
                   q.w, -q.z,  q.y, q.x );
}

float map( in vec3 pos, float time )
{
    // take a 3D slice
    vec4 p = vec4(pos,0.5*sin(time*0.513));
    
    // rotate 3D point into 4D
	vec4 q1 = normalize( cos( 0.2*time*vec4(1.0,1.7,1.1,1.5) + vec4(0.0,1.0,5.0,4.0) ) );
	vec4 q2 = normalize( cos( 0.2*time*vec4(1.9,1.7,1.4,1.3) + vec4(3.0,2.0,6.0,5.0) ) );
    p = q2m(q2)*p*q2m(q1);
    
    // 4D box
    return sdBox( p, vec4(0.8,0.5,0.7,0.2) )- 0.03;
}

// http://iquilezles.org/www/articles/normalsSDF/normalsSDF.htm
vec3 calcNormal( in vec3 pos, in float time )
{
    vec2 e = vec2(1.0,-1.0)*0.5773;
    const float eps = 0.00025;
    return normalize( e.xyy*map( pos + e.xyy*eps, time ) + 
					  e.yyx*map( pos + e.yyx*eps, time ) + 
					  e.yxy*map( pos + e.yxy*eps, time ) + 
					  e.xxx*map( pos + e.xxx*eps, time ) );
}

// http://iquilezles.org/www/articles/rmshadows/rmshadows.htm
float calcSoftshadow( in vec3 ro, in vec3 rd, in float mint, in float tmax, float time )
{
    float res = 1.0;
    float t = mint;
    for( int i=0; i<128; i++ )
    {
		float h = map( ro + rd*t, time );
        res = min( res, 16.0*h/t );
        t += clamp( h, 0.01, 0.25 );
        if( res<0.001 || t>tmax ) break;
    }
    return clamp( res, 0.0, 1.0 );
}

vec2 intersect( in vec3 ro, in vec3 rd, in float time )
{
    vec2 res = vec2(1e20,-1.0);
    
    // plane
    {
    float t = (-1.0-ro.y)/rd.y;
    if( t>0.0 ) res = vec2(t,1.0);
    }

    {
    // box
    float tmax = min(6.0,res.x);
    float t = 0.4;
    for( int i=0; i<128; i++ )
    {
        vec3 pos = ro + t*rd;
        float h = map(pos, time);
        if( h<0.001 || t>tmax ) break;
        t += h;
    }
    if( t<tmax && t<res.x ) res = vec2(t,2.0);
    }
    
    return res;
}

// http://iquilezles.org/www/articles/checkerfiltering/checkerfiltering.htm
float checkersGradBox( in vec2 p, in vec2 dpdx, in vec2 dpdy )
{
    // filter kernel
    vec2 w = abs(dpdx)+abs(dpdy) + 0.001;
    // analytical integral (box filter)
    vec2 i = 2.0*(abs(fract((p-0.5*w)*0.5)-0.5)-abs(fract((p+0.5*w)*0.5)-0.5))/w;
    // xor pattern
    return 0.5 - 0.5*i.x*i.y;                  
}

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
        float di = 0.5*sin(fragCoord.x*147.0)*sin(fragCoord.y*131.0);
        float time = iTime - 0.5*(1.0/24.0)*(float(m*AA+n)+di)/float(AA*AA-1);
        
        #else    
        vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
        float time = iTime;
        #endif

	    // create view ray
        vec3 ro = vec3(-0.5,0.0,2.5);
        vec3 rd = normalize( vec3(p,-1.8) );
         // ray differentials
        vec2 px = (-iResolution.xy+2.0*(fragCoord.xy+vec2(1.0,0.0)))/iResolution.y;
        vec2 py = (-iResolution.xy+2.0*(fragCoord.xy+vec2(0.0,1.0)))/iResolution.y;
        vec3 rdx = normalize( vec3(px,-1.8) );
        vec3 rdy = normalize( vec3(py,-1.8) );

        // raymarch
        vec2 tm = intersect( ro, rd, time );
        vec3 col = vec3(0.6,0.75,0.85) - 0.97*rd.y;
        if( tm.y>0.0 )
        {
            // shading/lighting	
            vec3 pos = ro + tm.x*rd;
            vec3 nor = (tm.y<1.5)?vec3(0.0,1.0,0.0):calcNormal(pos,time);
            vec3 lig = normalize(vec3(0.8,0.4,0.6));
            float dif = clamp( dot(nor,lig), 0.0, 1.0 );
            vec3  hal = normalize(lig-rd);
            float sha = calcSoftshadow( pos+0.001*nor, lig, 0.001, 4.0, time );
            float amb = 0.6 + 0.4*nor.y;
            float bou = clamp(-nor.y,0.0,1.0);
            float spe = clamp(dot(nor,hal),0.0,1.0);
            col  = 3.5*vec3(1.00,0.80,0.60)*dif*sha;
            col += 4.0*vec3(0.12,0.18,0.24)*amb;
            col += 2.0*vec3(0.30,0.20,0.10)*bou;
            
            if( pos.y<-.99 )
            {
                // project pixel footprint into the plane
                vec3 dpdx = ro.y*(rd/rd.y-rdx/rdx.y);
                vec3 dpdy = ro.y*(rd/rd.y-rdy/rdy.y);
                float f = checkersGradBox( 2.0*pos.xz, 2.0*dpdx.xz, 2.0*dpdy.xz );
                col *= 0.2 + f*vec3(0.05);
            }
            else
            {
                col *= 0.25;
            }
            
            col += 0.2*pow(spe,8.0)*dif*sha;
            
            col = mix( col, vec3(0.6,0.7,0.8), 1.0-exp(-0.001*tm.x*tm.x) );           
        }

        // gamma        
	    tot += col;
    #if AA>1
    }
    tot /= float(AA*AA);
    #endif

    tot = pow( tot, vec3(0.45) );
    
    tot = clamp(tot,0.0,1.0);
        
    tot = tot*tot*(3.0-2.0*tot);

    fragColor = vec4( tot, 1.0 );
}'''


class St4DRoundedBox(GlslSource):
    name = 'st_4_d_rounded_box'
    label = '4 D Rounded Box'
    description = 'I raymarched a 3D slice of a 4D box. The 3D slice (plane) that cuts the 4D box is animated over time, and the cube itself is rotating in 4D space. Note this is...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
