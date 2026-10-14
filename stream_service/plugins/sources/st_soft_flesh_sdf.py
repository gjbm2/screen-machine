"""Soft Flesh SDF: more tests

Source: https://www.shadertoy.com/view/NlfGWs
Author: ENDESGA
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2018 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Smooth vs sharp boolean operations for combining shapes

// Related techniques:
//
// Elongation  : https://www.shadertoy.com/view/Ml3fWj
// Rounding    : https://www.shadertoy.com/view/Mt3BDj
// Onion       : https://www.shadertoy.com/view/MlcBDj
// Metric      : https://www.shadertoy.com/view/ltcfDj
// Combination : https://www.shadertoy.com/view/lt3BW2
// Repetition  : https://www.shadertoy.com/view/3syGzz
// Extrusion2D : https://www.shadertoy.com/view/4lyfzw
// Revolution2D: https://www.shadertoy.com/view/4lyfzw
//
// More information here: http://iquilezles.org/www/articles/distfunctions/distfunctions.htm


float opUnion( float d1, float d2 )
{
    return min(d1,d2);
}

float opSubtraction( float d1, float d2 )
{
    d1 *= -1.0;
    return (d1 > d2) ? d1 : d2;
}

float opIntersection( float d1, float d2 )
{
    return max(d1,d2);
}

void pR45(inout vec2 p) {
	p = (p + vec2(p.y, -p.x))*sqrt(0.5);
}

float pMod1(inout float p, float size) {
	float halfsize = size*0.5;
	float c = floor((p + halfsize)/size);
	p = mod(p + halfsize, size) - halfsize;
	return c;
}

float opSmoothUnion(float a, float b, float k)
{
    float h = max(k-abs(a-b),0.0);
    return min(a, b) - h*h*0.25/k;
    //k *= 0.15;
    //return max(a, (a + k - abs(b))*sqrt(0.5));
    /*float n = 4.0;
    if ((a < r) && (b < r)) {
		vec2 p = vec2(a, b);
		float columnradius = r*sqrt(2.0)/((n-1.0)*2.0+sqrt(2.0));
		pR45(p);
		p.x -= sqrt(2.0)/2.0*r;
		p.x += columnradius*sqrt(2.0);
		if (mod(n,2.0) == 1.0) {
			p.y += columnradius;
		}
		// At this point, we have turned 45 degrees and moved at a point on the
		// diagonal that we want to place the columns on.
		// Now, repeat the domain along this direction and place a circle.
		pMod1(p.y, columnradius*2.0);
		float result = length(p) - columnradius;
		result = min(result, p.x);
		result = min(result, a);
		return min(result, b);
	} else {
		return min(a, b);
	}*/
}

float opSmoothSubtraction( float d1, float d2, float k )
{
    float h = max(k-abs(-d1-d2),0.0);
    return max(-d1, d2) + h*h*0.25/k;
}

float opSmoothIntersection( float d1, float d2, float k )
{
    float h = max(k-abs(d1-d2),0.0);
    return max(d1, d2) + h*h*0.25/k;
}

vec3 rotate(vec3 uv, float th) {
  return vec3(mat2(cos(th), sin(th), -sin(th), cos(th)) * uv.xy, uv.z);
}

//-------------------------------------------------

float sdSphere( in vec3 p, in float r )
{
    //float d = 0.0, d2 = 0.0;
    p = rotate(p,iTime);
    const float phi = (1.+sqrt(5.))*.5;
    p = abs(p);
    p += phi*p.zxy;
    return ((max(max(p.x,p.y),p.z)-r*phi) / sqrt(phi*phi+1.));
    //r *= 2.0;
    //d2 = ((max(max(p.x,p.y),p.z)-r*phi) / sqrt(phi*phi+1.)) * 0.1;
    //return length(vec2(d, d2)) - 0.02;
}


float sdRoundBox( vec3 p, vec3 b, float r )
{
  vec3 d = abs(p) - b;
  return min(max(d.x,max(d.y,d.z)),0.0) + length(max(d,0.0)) - r;
}

//---------------------------------


float map(in vec3 pos)
{
    float d = 1e10;
    
    
    float an = sin(iTime) * 2.5;
    
    // opSmoothUnion
    {
    vec3 q = pos - vec3(0.0,0.0,1.0);
    float d1 = sdSphere( q-vec3(0.0,2.5+0.3*an,0.0), 2.5 );
    float d2 = sdRoundBox(q - vec3(0.0,-4.0,0.0), vec3(4.5,4.0,1.0), 0.1 ); 
    float dt = opSmoothSubtraction(d1,d2, 3.0) - 0.2;
    d = min( d, dt );
    }

    return d;
}

// http://iquilezles.org/www/articles/normalsSDF/normalsSDF.htm
vec3 calcNormal( in vec3 pos )
{
    const float ep = 0.0001;
    vec2 e = vec2(1.0,-1.0)*0.5773;
    return normalize( e.xyy*map( pos + e.xyy*ep ) + 
					  e.yyx*map( pos + e.yyx*ep ) + 
					  e.yxy*map( pos + e.yxy*ep ) + 
					  e.xxx*map( pos + e.xxx*ep ) );
}

// http://iquilezles.org/www/articles/rmshadows/rmshadows.htm
float calcSoftshadow( in vec3 ro, in vec3 rd, float tmin, float tmax, const float k )
{
	float res = 7777777.7777;
    float t = tmin;
    for( int i=0; i<256; i++ )
    {
		float h = map( ro + rd*t );
        res = min( res * 0.777, k*h/t );
        t += clamp( h, 0.02, 0.20 );
        if( res<0.005 || t>tmax ) break;
    }
    return clamp( res, 0.0, 1.0 );
}


#define AA 2

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
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
 
        vec3 ro = vec3(0.0,4.0,8.0);
        vec3 rd = normalize(vec3(p-vec2(0.0,1.8),-3.5));

        float t = 7.0;
        for( int i=0; i<64; i++ )
        {
            vec3 p = ro + t*rd;
            float h = map(p);
            if( abs(h)<0.001 || t>11.0 ) break;
            t += h;
        }

        vec3 col = vec3(0.0);

        if( t<11.0 )
        {
            vec3 pos = ro + t*rd;
            vec3 nor = calcNormal(pos);
            vec3  lig = normalize(vec3(1.0,0.8,-0.2));
            float dif = clamp(dot(nor,lig),0.0,1.0);
            float sha = calcSoftshadow( pos, lig, 0.1, 4.0, 32.0 );
            float amb = 0.5 + 0.5*nor.y;
            col = vec3(0.5,0.0,0.25)*amb + 
                  vec3(1.00,0.9,0.80)*dif*sha;
        }

        col = sqrt( col );
	    tot += col;
    #if AA>1
    }
    tot /= float(AA*AA);
    #endif

    tot += tot * 0.25;
    tot *= tot * tot * tot * tot * tot;

	fragColor = vec4( tot * 2.0, 1.0 );
}'''


class StSoftFleshSdf(GlslSource):
    name = 'st_soft_flesh_sdf'
    label = 'Soft Flesh SDF'
    description = 'more tests'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
