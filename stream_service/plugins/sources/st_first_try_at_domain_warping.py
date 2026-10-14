"""First try at domain warping: dw

Source: https://www.shadertoy.com/view/7tBSWG
Author: wnu
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // * The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''#define PI 3.1415926538

float random (in vec2 _st) {
    return fract(sin(dot(_st.xy,
                         vec2(12.9898,78.233)))*
        43758.5453123);
}

vec2 hash( vec2 p ) // replace this by something better
{
	p = vec2( dot(p,vec2(127.1,311.7)), dot(p,vec2(269.5,183.3)) );
	return -1.0 + 2.0*fract(sin(p)*43758.5453123);
}

float simnoise( in vec2 p )
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
    return dot( n, vec3(70.0) )*0.5;
}

// * The MIT License
// * Copyright © 2013 Nikita Miropolskiy

vec3 random3(vec3 c) {
	float j = 4096.0*sin(dot(c,vec3(17.0, 59.4, 15.0)));
	vec3 r;
	r.z = fract(512.0*j);
	j *= .125;
	r.x = fract(512.0*j);
	j *= .125;
	r.y = fract(512.0*j);
	return r-0.5;
}

/* skew constants for 3d simplex functions */
const float F3 =  0.3333333;
const float G3 =  0.1666667;

/* 3d simplex noise */
float simplex3d(vec3 p) {
	 /* 1. find current tetrahedron T and it's four vertices */
	 /* s, s+i1, s+i2, s+1.0 - absolute skewed (integer) coordinates of T vertices */
	 /* x, x1, x2, x3 - unskewed coordinates of p relative to each of T vertices*/
	 
	 /* calculate s and x */
	 vec3 s = floor(p + dot(p, vec3(F3)));
	 vec3 x = p - s + dot(s, vec3(G3));
	 
	 /* calculate i1 and i2 */
	 vec3 e = step(vec3(0.0), x - x.yzx);
	 vec3 i1 = e*(1.0 - e.zxy);
	 vec3 i2 = 1.0 - e.zxy*(1.0 - e);
	 	
	 /* x1, x2, x3 */
	 vec3 x1 = x - i1 + G3;
	 vec3 x2 = x - i2 + 2.0*G3;
	 vec3 x3 = x - 1.0 + 3.0*G3;
	 
	 /* 2. find four surflets and store them in d */
	 vec4 w, d;
	 
	 /* calculate surflet weights */
	 w.x = dot(x, x);
	 w.y = dot(x1, x1);
	 w.z = dot(x2, x2);
	 w.w = dot(x3, x3);
	 
	 /* w fades from 0.6 at the center of the surflet to 0.0 at the margin */
	 w = max(0.6 - w, 0.0);
	 
	 /* calculate surflet components */
	 d.x = dot(random3(s), x);
	 d.y = dot(random3(s + i1), x1);
	 d.z = dot(random3(s + i2), x2);
	 d.w = dot(random3(s + 1.0), x3);
	 
	 /* multiply d by w^4 */
	 w *= w;
	 w *= w;
	 d *= w;
	 
	 /* 3. return the sum of the four surflets */
	 return dot(d, vec4(52.0));
}


float noise (in vec2 _st) {
    vec2 i = floor(_st);
    vec2 f = fract(_st);

    // Four corners in 2D of a tile
    float a = random(i);
    float b = random(i + vec2(1.0, 0.0));
    float c = random(i + vec2(0.0, 1.0));
    float d = random(i + vec2(1.0, 1.0));

    vec2 u = f * f * (3.0 - 2.0 * f);

    return mix(a, b, u.x) +
            (c - a)* u.y * (1.0 - u.x) +
            (d - b) * u.x * u.y;
}

vec2 skew (vec2 st) {
    vec2 r = vec2(0.0);
    r.x = 1.1547*st.x;
    r.y = st.y+0.5*r.x;
    return r;
}

vec3 simplexGrid (vec2 st) {
    vec3 xyz = vec3(0.0);

    vec2 p = fract(skew(st));
    if (p.x > p.y) {
        xyz.xy = 1.0-vec2(p.x,p.y-p.x);
        xyz.z = p.y;
    } else {
        xyz.yz = 1.0-vec2(p.x-p.y,p.y);
        xyz.x = p.x;
    }

    return fract(xyz);
}

vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec2 mod289(vec2 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec3 permute(vec3 x) { return mod289(((x*34.0)+1.0)*x); }

float snoise(vec2 v) {

    // Precompute values for skewed triangular grid
    const vec4 C = vec4(0.211324865405187,
                        // (3.0-sqrt(3.0))/6.0
                        0.366025403784439,
                        // 0.5*(sqrt(3.0)-1.0)
                        -0.577350269189626,
                        // -1.0 + 2.0 * C.x
                        0.024390243902439);
                        // 1.0 / 41.0

    // First corner (x0)
    vec2 i  = floor(v + dot(v, C.yy));
    vec2 x0 = v - i + dot(i, C.xx);

    // Other two corners (x1, x2)
    vec2 i1 = vec2(0.0);
    i1 = (x0.x > x0.y)? vec2(1.0, 0.0):vec2(0.0, 1.0);
    vec2 x1 = x0.xy + C.xx - i1;
    vec2 x2 = x0.xy + C.zz;

    // Do some permutations to avoid
    // truncation effects in permutation
    i = mod289(i);
    vec3 p = permute(
            permute( i.y + vec3(0.0, i1.y, 1.0))
                + i.x + vec3(0.0, i1.x, 1.0 ));

    vec3 m = max(0.5 - vec3(
                        dot(x0,x0),
                        dot(x1,x1),
                        dot(x2,x2)
                        ), 0.0);

    m = m*m ;
    m = m*m ;

    // Gradients:
    //  41 pts uniformly over a line, mapped onto a diamond
    //  The ring size 17*17 = 289 is close to a multiple
    //      of 41 (41*7 = 287)

    vec3 x = 2.0 * fract(p * C.www) - 1.0;
    vec3 h = abs(x) - 0.5;
    vec3 ox = floor(x + 0.5);
    vec3 a0 = x - ox;

    // Normalise gradients implicitly by scaling m
    // Approximation of: m *= inversesqrt(a0*a0 + h*h);
    m *= 1.79284291400159 - 0.85373472095314 * (a0*a0+h*h);

    // Compute final noise value at P
    vec3 g = vec3(0.0);
    g.x  = a0.x  * x0.x  + h.x  * x0.y;
    g.yz = a0.yz * vec2(x1.x,x2.x) + h.yz * vec2(x1.y,x2.y);
    return 130.0 * dot(m, g);
}

float ridgeNoise(vec2 _st, float t){
    float n = abs(simnoise(_st));
    while(n > t){
        n = t - (n - t);
        if(n < 0.) {n = -n;}
    }
    
    return pow(n/t,0.31); // this value is good for tweaking
    
}

float ridgeNoise3(vec3 _st, float t){
    float n = abs(simplex3d(_st));
    while(n > t){
        n = t - (n - t);
        if(n < 0.) {n = -n;}
    }
    
    return pow(n/t,0.31); // this value is good for tweaking
    
}



#define NUM_OCTAVES 3

float fbm ( in vec2 _st) {
    float v = 0.0;
    float a = 0.5;
    vec2 shift = vec2(100.0);
    float offset = 0.;
    for (int i = 0; i < NUM_OCTAVES; ++i) {
        //v += a * sin((_st.y + _st.x+ _st.y)*10.)*1.5;
        offset += a*2.5;
        v += a * ridgeNoise(_st,0.3)*2.5;
        _st = _st * 2. + shift;
        a *= 0.5;
    }
    return v/offset;
}

float fbm3 ( in vec3 _st) {
    float v = 0.0;
    float a = 0.5;
    vec3 shift = vec3(100.0);
    float offset = 0.;
    for (int i = 0; i < NUM_OCTAVES; ++i) {
        //v += a * sin((_st.y + _st.x+ _st.y)*10.)*1.5;
        offset += a*2.5;
        v += a * ridgeNoise3(_st,0.3)*2.5;
        _st = _st * 2. + shift;
        a *= 0.5;
    }
    return v/offset;
}

float fbmN(vec2 _st, int n){
	float v = 0.;
    for (int i = 0;i<4;i++){
        if(i>=n) break;
        v= fbm(_st + v*4.);
    }
    
    return v;
}

float thresh = 0.5;

vec2 warp (vec2 p, float maxDist){
    float angle = fbm(p*4.1) * PI*2.;
    float dist = fbm(p*4.) * maxDist;// dist high angle gives like acid like look, reverse gives whisps?
    vec2 offset = vec2(cos(angle),sin(angle))*dist;
    return p + offset;
}

vec2 warp3 (vec3 p, float maxDist){
    float angle = fbm3(p*4.1) * PI*2.;
    float dist = fbm3(p*4.) * maxDist;// dist high angle gives like acid like look, reverse gives whisps?
    vec2 offset = vec2(cos(angle),sin(angle))*dist;
    return p.xy + offset;
}

float noise2(vec2 p){
    return ridgeNoise(warp(p,0.1),thresh);
}

vec2 warp2 (vec2 p, float maxDist){
    float angle = noise2(p*4.1) * PI*2.;
    float dist = noise2(p*4.) * maxDist;// dist high angle gives like acid like look, reverse gives whisps?
    vec2 offset = vec2(cos(angle),sin(angle))*dist;
    return p + offset;
}

vec3 hsl2rgb( in vec3 c )
{
    vec3 rgb = clamp( abs(mod(c.x*6.0+vec3(0.0,4.0,2.0),6.0)-3.0)-1.0, 0.0, 1.0 );

    return c.z + c.y * (rgb-0.5)*(1.0-abs(2.0*c.z-1.0));
}


void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // Normalized pixel coordinates (from 0 to 1)
    vec2 R = iResolution.xy;
    vec2 uv = (fragCoord/R -0.5);
    uv.x *= R.x/R.y;

    // Time varying pixel color
    //vec3 col = vec3(noise(uv*10.));
    //vec3 col = vec3(ridgeNoise(uv*2.,0.55));
    
    uv = warp3(vec3(uv*1.,iTime/20.),0.08);
    
    float diam = 0.5;
    float e = 0.5;
    float circle = smoothstep(diam,diam - e,length(uv));
    //maybe use somethnig other than smooth step to only smooth the bottom
    
    
    vec3 col = vec3(circle);
    col = vec3(hsl2rgb(vec3(circle*0.4-0.25,0.8,circle)))*circle;
    col = 1. - col;
    col.r = pow(col.r,0.6);
    //col = vec3(noise2(uv*1.));
    //col = vec3(ridgeNoise(uv*10.,0.3));
    //col = vec3(fbm(uv*5.));
    // Output to screen
    fragColor = vec4(col,1.0);
}'''


class StFirstTryAtDomainWarping(GlslSource):
    name = 'st_first_try_at_domain_warping'
    label = 'First try at domain warping'
    description = 'dw'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
