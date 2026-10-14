"""Quad   distance 3 D: Distance field to a quad, petty much an extension to [url]https://www.shadertoy.com/view/4sXXRN[/url]

Source: https://www.shadertoy.com/view/Md2BWW
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

// In case a mesh was rendered, only one square root would be needed for the
// whole mesh. In this example the quad is given a thickness of 0.01 units,
// for display purposes. Like the square root, this thickness should be added
// only once for the whole mesh too.

// List of other 3D SDFs: https://www.shadertoy.com/playlist/43cXRl
//
// and http://iquilezles.org/www/articles/distfunctions/distfunctions.htm


float dot2( in vec3 v ) { return dot(v,v); }

float udQuad( in vec3 v1, in vec3 v2, in vec3 v3, in vec3 v4, in vec3 p )
{
    #if 1
    // handle ill formed quads
    if( dot( cross( v2-v1, v4-v1 ), cross( v4-v3, v2-v3 )) < 0.0 )
    {
        vec3 tmp = v3;
        v3 = v4;
        v4 = tmp;
    }
    #endif

    
    vec3 v21 = v2 - v1; vec3 p1 = p - v1;
    vec3 v32 = v3 - v2; vec3 p2 = p - v2;
    vec3 v43 = v4 - v3; vec3 p3 = p - v3;
    vec3 v14 = v1 - v4; vec3 p4 = p - v4;
    vec3 nor = cross( v21, v14 );

    return sqrt( (sign(dot(cross(v21,nor),p1)) + 
                  sign(dot(cross(v32,nor),p2)) + 
                  sign(dot(cross(v43,nor),p3)) + 
                  sign(dot(cross(v14,nor),p4))<3.0) 
                  ?
                  min( min( dot2(v21*clamp(dot(v21,p1)/dot2(v21),0.0,1.0)-p1), 
                            dot2(v32*clamp(dot(v32,p2)/dot2(v32),0.0,1.0)-p2) ), 
                       min( dot2(v43*clamp(dot(v43,p3)/dot2(v43),0.0,1.0)-p3),
                            dot2(v14*clamp(dot(v14,p4)/dot2(v14),0.0,1.0)-p4) ))
                  :
                  dot(nor,p1)*dot(nor,p1)/dot2(nor) );
}

//=====================================================

float map( in vec3 p )
{
    // triangle	
	vec3 v1 = 1.5*cos( iTime*1.1 + vec3(0.0,1.0,1.0) + 0.0 );
	vec3 v2 = 1.0*cos( iTime*1.2 + vec3(0.0,2.0,3.0) + 2.0 );
	vec3 v3 = 1.0*cos( iTime*1.3 + vec3(0.0,3.0,5.0) + 4.0 );
    vec3 v4 = v1 + ( v3 - v2);
	float d1 = udQuad( v1, v2, v3, v4, p ) - 0.01;

    // ground plane
	float d2 = p.y + 1.0;

    return min( d1, d2 );	
}

float intersect( in vec3 ro, in vec3 rd )
{
	const float maxd = 10.0;
	float h = 1.0;
    float t = 0.0;
    for( int i=0; i<50; i++ )
    {
        if( h<0.001 || t>maxd ) break;
	    h = map( ro+rd*t );
        t += h;
    }

    if( t>maxd ) t=-1.0;
	
    return t;
}

vec3 calcNormal( in vec3 pos )
{
    vec3 eps = vec3(0.002,0.0,0.0);

	return normalize( vec3(
           map(pos+eps.xyy) - map(pos-eps.xyy),
           map(pos+eps.yxy) - map(pos-eps.yxy),
           map(pos+eps.yyx) - map(pos-eps.yyx) ) );
}

float calcSoftshadow( in vec3 ro, in vec3 rd, float k )
{
    float res = 1.0;
    float t = 0.0;
	float h = 1.0;
    for( int i=0; i<20; i++ )
    {
        h = map(ro + rd*t);
        res = min( res, k*h/t );
		t += clamp( h, 0.01, 1.0 );
		if( h<0.0001 ) break;
    }
    return clamp(res,0.0,1.0);
}

float calcOcclusion( in vec3 pos, in vec3 nor )
{
    float occ = 0.0;
    float sca = 1.0;
    for( int i=0; i<5; i++ )
    {
        float hr = 0.02 + 0.025*float(i*i);
        vec3 aopos =  nor * hr + pos;
        float dd = map( aopos );
        occ += -(dd-hr)*sca;
        sca *= 0.95;
    }
    return 1.0 - clamp( occ, 0.0, 1.0 );
}

vec3 lig = normalize(vec3(1.0,0.9,0.7));

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;

	vec3 ro = vec3(0.0, 0.25, 2.0 );
	vec3 rd = normalize( vec3(p,-1.0) );
	
	vec3 col = vec3(0.0);

    float t = intersect(ro,rd);
    if( t>0.0 )
    {
        vec3 pos = ro + t*rd;
        vec3 nor = calcNormal(pos);
		float sha = calcSoftshadow( pos + nor*0.01, lig, 32.0 );
		float occ = calcOcclusion( pos, nor );
		col =  vec3(0.9,0.6,0.3)*clamp( dot( nor, lig ), 0.0, 1.0 ) * sha;
		col += vec3(0.5,0.6,0.7)*clamp( nor.y, 0.0, 1.0 )*occ;
        col += 0.03;
		col *= exp( -0.2*t );
        col *= 1.0 - smoothstep( 5.0, 10.0, t );
	}

	col = pow( clamp(col,0.0,1.0), vec3(0.45) );
	   
    fragColor = vec4( col, 1.0 );
}

'''


class StQuadDistance3D(GlslSource):
    name = 'st_quad_distance_3_d'
    label = 'Quad   distance 3 D'
    description = 'Distance field to a quad, petty much an extension to [url]https://www.shadertoy.com/view/4sXXRN[/url]'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.35)
