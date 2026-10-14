"""Alpha Tower: A quick Alpha Tower for you all! :) Enjoy! fixed the glitch by adding the substepping :)

Source: https://www.shadertoy.com/view/4t2cDW
Author: Del
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2013 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
    

// tower & fruit machine reels added by Del


#define AA 1   // make this 1 is your machine is too slow

#define PI 3.1415926
#define DEG2RAD ((PI * 2.0) / 360.0)
float tBlend = -1.0;
//------------------------------------------------------------------

float sdPlane( vec3 p )
{
	return p.y;
}

float sdCylinder( vec3 p, vec2 h )
{
  vec2 d = abs(vec2(length(p.xz),p.y)) - h;
  return min(max(d.x,d.y),0.0) + length(max(d,0.0));
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

// Repeat only a few times: from indices <start> to <stop> (similar to above, but more flexible)
float pModInterval1(inout float p, float size, float start, float stop) {
	float halfsize = size*0.5;
	float c = floor((p + halfsize)/size);
	p = mod(p+halfsize, size) - halfsize;
	if (c > stop) { //yes, this might not be the best thing numerically.
		p += size*(c - stop);
		c = stop;
	}
	if (c <start) {
		p += size*(c - start);
		c = start;
	}
	return c;
}


// Repeat around the origin by a fixed angle.
// For easier use, num of repetitions is use to specify the angle.
float pModPolar(inout vec2 p, float repetitions) {
	float angle = 2.0*PI/repetitions;
	float a = atan(p.y, p.x) + angle/2.;
	float r = length(p);
	float c = floor(a/angle);
	a = mod(a,angle) - angle/2.;
	p = vec2(cos(a), sin(a))*r;
	// For an odd number of repetitions, fix cell index of the cell in -x direction
	// (cell index would be e.g. -5 and 5 in the two halves of the cell):
	if (abs(c) >= (repetitions/2.0)) c = abs(c);
	return c;
}


//---------------------------------------------------------
vec3 rotateX(vec3 p, float a)
{
  float sa = sin(a);
  float ca = cos(a);
  return vec3(p.x, ca * p.y - sa * p.z, sa * p.y + ca * p.z);
}
vec3 rotateY(vec3 p, float a)
{
  float sa = sin(a);
  float ca = cos(a);
  return vec3(ca * p.x + sa * p.z, p.y, -sa * p.x + ca * p.z);
}
vec3 rotateZ(vec3 p, float a)
{
  float sa = sin(a);
  float ca = cos(a);
  return vec3(ca * p.x - sa * p.y, sa * p.x + ca * p.y, p.z);
}



#define XP 0.0
#define ZP 0.0
#define HEIGHT 0.4
#define FONTTHICKNESS 0.1
#define INNER 1.9

struct ReelData
{
  float rot;
};

vec3 p00 = vec3(XP, 2.7, ZP);

ReelData dat[4] = ReelData[4](
    ReelData(0.0),
    ReelData(0.0),
    ReelData(0.0),
    ReelData(0.0)
);
    
// crap stuff
// dirty... not sure if I am doing this correctly...
vec4 SampleFontTex(vec2 uv,float xoff)
{
    uv = uv.yx;
    uv.x = -uv.x;
//    uv += 0.04;
    uv.x += 0.04 + (0.25 * xoff);
    uv *= 0.25;
    vec2 fl = floor(uv + 0.5);
    uv = fl + fract(uv+0.5)-0.5;
    
    // Sample the font texture. Make sure to not use mipmaps.
    // Add a small amount to the distance field to prevent a strange bug on some gpus. Slightly mysterious. :(
    //return texture(iChannel0, (uv+0.5)*((1.0/16.0)*8.0), -100.0) + vec4(0.0, 0.0, 0.0, 0.00001);
    return texture(iChannel0, uv, -100.0) + vec4(0.0, 0.0, 0.0, 0.00001);
}


//------------------------------------------------------------------

vec2 reel (in vec3 pos)
{
   	float _mat1 = 230.0;	// tower
	float _mat2 = 20.0;	// font

    float sv = 4.0 * sin(pos.y*0.1+dat[1].rot*0.01);
    
    float cval = abs(pos.y)*12.0;
    
// Repeat only a few times: from indices <start> to <stop> (similar to above, but more flexible)
    float _index = pModInterval1(pos.y,1.0,-3.0,3.0);
    int index = int(abs(_index)) % 4;
    
	pos = rotateY (pos,sv );	// sin movement (pixel)
    
//    pos = rotateY (pos, sin(dat[index].rot*0.02));	// sin movement (reel)
//	pos = rotateY (pos,  DEG2RAD*dat[index].rot);	// spin
    
    float innercylinderdist = sdCylinder(pos,vec2(INNER,HEIGHT));
    float d1 = sdCylinder(pos,vec2(INNER+FONTTHICKNESS,HEIGHT));
 	// Load the font texture's distance field.
	vec4 _c = SampleFontTex( vec2(atan(pos.x,pos.z), 0.5+(pos.y*0.75)) / PI, 13.0);
    float letterDistField = _c.w - 0.5+1.0/256.0;
    d1 = max(letterDistField,d1);
    
    vec2 res = vec2( d1, _mat2 + cval);
    
    d1 = sdCylinder(pos,vec2(INNER-0.01,HEIGHT+0.1));
    res.x = opS(res.x, d1 );
    
    res = opU(res, vec2( d1, _mat1 ) );
    return res;
    
}





//------------------------------------------------------------------
// old alpha tower version...
const vec3 p01 = vec3(XP+0.0,5.95, ZP+0.0);

vec4 SampleFontTex(vec2 uv)
{
    vec2 fl = floor(uv + 0.5);
    uv = fl + fract(uv+0.5)-0.5;
    // Sample the font texture. Make sure to not use mipmaps.
    // Add a small amount to the distance field to prevent a strange bug on some gpus. Slightly mysterious. :(
    return texture(iChannel0, (uv+0.5)*((1.0/16.0)*8.0), -100.0) + vec4(0.0, 0.0, 0.0, 0.00001);
}

vec2 _maptower( in vec3 pos )
{
	vec3 r1, r2;
	float d1 = 0.0f;
	float d2 = 0.0f;
	float _mat1 = 230.0;	// tower
	float _mat2 = 48.0;	// font
       
	vec2 res = vec2( sdPlane(pos), 1.0 ); 			// floor (checker)
    
	r1 = rotateY (pos-p01,  dat[0].rot*0.02);
	r1 = rotateX (r1,  DEG2RAD*0.0);
    d1 = sdCylinder(r1,vec2(1.90,6.0));
    res = opU(res, vec2( d1, _mat1 ) );

    d1 = sdCylinder(r1,vec2(2.0,6.0));
 	// Load the font texture's distance field.
	vec4 _c = SampleFontTex( vec2(atan(r1.x,r1.z), r1.y) / PI);
    float letterDistField = _c.w - 0.5+1.0/256.0;
    d1 = max(letterDistField,d1);
    
    float cval = pos.y*30.0;
    res = opU(res, vec2( d1, _mat2+ cval)  );
    
    // More accuracy (substepping)
	res.x *= 0.85;
    
	return res;
}



vec2 map( in vec3 pos )
{
    
    vec2 res = vec2( sdPlane(pos), 1.0 ); 			// floor (checker)

    vec2 res2 = res;
    res2 =  opU(res2,_maptower(pos));
    

    pos = rotateX (pos-p00,  DEG2RAD*270.0);

    res =  opU(res,reel(pos));

    // More accuracy (substepping)
	res.x *= 0.85;

    // blend the scenes...
    return mix(res2, res, tBlend);
    
    
//	res.x *= 0.85;
//	return res;
}

vec2 castRay( in vec3 ro, in vec3 rd )
{
    float tmin = 1.0;
    float tmax = 20.0;
       
    float t = tmin;
    float m = -1.0;
    for( int i=0; i<64; i++ )
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
        vec3 ref = reflect( rd, nor );
        
        // material        
		col = 0.45 + 0.35*sin( vec3(0.05,0.08,0.10)*(m-1.0) );
        if( m<1.5 )
        {
            
            float f = mod( floor(0.5*pos.z) + floor(0.5*pos.x), 2.0);
            col = 0.3 + 0.1*f*vec3(1.0);
        }
#if 1
        // lighting        
        float occ = calcAO( pos, nor );
		vec3  lig = normalize( vec3(-0.4, 0.7, -0.6) );
		float amb = clamp( 0.5+0.5*nor.y, 0.0, 1.0 );
        float dif = clamp( dot( nor, lig ), 0.0, 1.0 );
        float bac = clamp( dot( nor, normalize(vec3(-lig.x,0.0,-lig.z))), 0.0, 1.0 )*clamp( 1.0-pos.y,0.0,1.0);
        float dom = smoothstep( -0.1, 0.1, ref.y );
        float fre = pow( clamp(1.0+dot(nor,rd),0.0,1.0), 2.0 );
		float spe = pow(clamp( dot( ref, lig ), 0.0, 1.0 ),16.0);
        
        dif *= softshadow( pos, lig, 0.02, 2.5 );
        dom *= softshadow( pos, ref, 0.02, 2.5 );

		vec3 lin = vec3(0.0);
        lin += 1.30*dif*vec3(1.00,0.80,0.55);
		lin += 2.00*spe*vec3(1.00,0.90,0.70)*dif;
        lin += 0.40*amb*vec3(0.40,0.60,1.00)*occ;
        lin += 0.50*dom*vec3(0.40,0.60,1.00)*occ;
        lin += 0.50*bac*vec3(0.25,0.25,0.25)*occ;
        lin += 0.25*fre*vec3(1.00,1.00,1.00)*occ;
		col = col*lin;
#endif
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
    return mat3( cu, cv, cw);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 mo = iMouse.xy/iResolution.xy;
    if (iMouse.z<0.5)
        mo = vec2((180.0 * DEG2RAD)/6.0,0.0);
    
	// blend...
	tBlend = cos(iTime*0.4);
    tBlend*=2.0f;
    
    tBlend = clamp(tBlend,0.0,1.0);
        
	float time = iTime;
    dat[0].rot += time*25.0;
    dat[1].rot += time*50.0;
    dat[2].rot += time*75.0;
    dat[3].rot += time*100.0;

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
        //vec3 ro = vec3( 6.5*cos(0.1*time + 6.0*mo.x), 1.0 + 2.0*mo.y, 0.5 + 6.5*sin(0.1*time + 6.0*mo.x) );
        
		float ang = 180.0 * DEG2RAD;        
        vec3 ro = vec3( 6.5*cos(ang+(6.0*mo.x)), 4.0 + 2.0*mo.y, 6.5*sin(ang+(6.0*mo.x)) );
        vec3 ta = vec3( 0.0, 2.0, 0.0 );
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


class StAlphaTower(GlslSource):
    name = 'st_alpha_tower'
    label = 'Alpha Tower'
    description = 'A quick Alpha Tower for you all! :) Enjoy! fixed the glitch by adding the substepping :)'
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'noise'}
    params = scene_params({}, render_scale=0.25)
