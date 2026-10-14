"""Starry background with nebula: License CC0: Starry background with nebula Created for another shader but thought the background could be useful to others so extracted it

Source: https://www.shadertoy.com/view/fdXXR4
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Starry background with nebula
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Starry background with nebula
//  Created for another shader but thought the background could be useful to others so extracted it

// Controls how many layers of stars
#define LAYERS            5.0

// QUINTIC or HERMITE interpolation?
#define QUINTIC

// How often to change the nebula
#define PERIOD            15.0

#define PI                3.141592654
#define TAU               (2.0*PI)
#define TIME              iTime
#define RESOLUTION        iResolution
#define ROT(a)            mat2(cos(a), sin(a), -sin(a), cos(a))
#define PCOS(x)           (0.5 + 0.5*cos(x))
#define TTIME             (TAU*TIME)

const mat2 rotSome          = ROT(1.0);

float tanh_approx(float x) {
//  return tanh(x);
  float x2 = x*x;
  return clamp(x*(27.0 + x2)/(27.0+9.0*x2), -1.0, 1.0);
}

float hash(float co) {
  return fract(sin(co*12.9898) * 13758.5453);
}

float hash(vec2 co) {
  co += 123.4;
  return fract(sin(dot(co, vec2(12.9898,58.233))) * 13758.5453);
}

vec2 hash2(vec2 p) {
  p = vec2 (dot (p, vec2 (127.1, 311.7)),
            dot (p, vec2 (269.5, 183.3)));

  return -1. + 2.*fract (sin (p)*43758.5453123);
}

// https://stackoverflow.com/questions/15095909/from-rgb-to-hsv-in-opengl-glsl
vec3 hsv2rgb(vec3 c) {
  const vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
  vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
  return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

// http://mercury.sexy/hg_sdf/
vec2 mod2(inout vec2 p, vec2 size) {
  vec2 c = floor((p + size*0.5)/size);
  p = mod(p + size*0.5,size) - size*0.5;
  return c;
}

vec3 toSpherical(vec3 p) {
  float r   = length(p);
  float t   = acos(p.z/r);
  float ph  = atan(p.y, p.x);
  return vec3(r, t, ph);
}

vec3 postProcess(vec3 col, vec2 q)  {
  col=pow(clamp(col,0.0,1.0),vec3(1.0/2.2)); 
  col=col*0.6+0.4*col*col*(3.0-2.0*col);  // contrast
  col=mix(col, vec3(dot(col, vec3(0.33))), -0.4);  // satuation
  col*=0.5+0.5*pow(19.0*q.x*q.y*(1.0-q.x)*(1.0-q.y),0.7);  // vigneting
  return col;
}

// From one of IQ's value noise shaders
float vnoise(vec2 x) {
  vec2 i = floor(x);
  vec2 w = fract(x);

#ifdef QUINTIC
  // quintic interpolation
  vec2 u = w*w*w*(w*(w*6.0-15.0)+10.0);
#else
  // cubic interpolation
  vec2 u = w*w*(3.0-2.0*w);
#endif    

  float a = hash(i+vec2(0.0,0.0));
  float b = hash(i+vec2(1.0,0.0));
  float c = hash(i+vec2(0.0,1.0));
  float d = hash(i+vec2(1.0,1.0));
    
  float k0 =   a;
  float k1 =   b - a;
  float k2 =   c - a;
  float k3 =   d - c + a - b;

  float aa = mix(a, b, u.x);
  float bb = mix(c, d, u.x);
  float cc = mix(aa, bb, u.y);
  
  return k0 + k1*u.x + k2*u.y + k3*u.x*u.y;
}

float globalCloudDensity(vec2 p, float off) {
  vec2 pp = p;

  p *= 3.33;

  float gcd = vnoise(p+off);
  gcd *= smoothstep(PI/2.0, PI/4.0, abs(pp.x));
  gcd *= smoothstep(PI/6.0, PI/18.0, abs(pp.y));

  return gcd;
}

float localCloudDensity(vec2 p, float off) {
  p *= 10.0;
  const float aa = -0.45;
  const mat2 pp = 2.03*rotSome;
  float a = 0.5;
  float s = 0.0;
  p += off;

  s += a*vnoise(p); a *= aa; p *= pp;
  s += a*vnoise(p); a *= aa; p *= pp;
  s += a*vnoise(p); a *= aa; p *= pp;
  s += a*vnoise(p); a *= aa; p *= pp;
  s += a*vnoise(p); a *= aa; p *= pp;
    
  return s*2.75;
}

vec3 clouds(vec3 ro, vec3 rd, out float cloudDensity) {
  vec3 srd = toSpherical(rd.zxy);
  float y = sin(srd.y);

  vec2 pp = srd.zy;
  pp.x *= y;
  pp.y -= PI/2.0;
  pp *= ROT(0.5);

  float h = hash(floor(2.0+TIME/PERIOD));
  float off = 10.0*fract(123.0*h)+100.0;

  float gcd = globalCloudDensity(pp, off);

  float cd = gcd*localCloudDensity(pp, off);
  float cdo = gcd*localCloudDensity(pp+00.075*vec2(0.125, -0.25), off);
  cloudDensity = cd;

  // Basis for some very fake shading
  float cli = mix(-0.5, 1.0, 0.5 + 0.5*tanh_approx(12.0*(cd-cdo)));
  
  float tc = clamp(cd, 0.0, 1.0);
  float huec = (mix(-0.2, 0.05, tc)+0.05)-0.15*(h-0.5)-0.0;
  float satc = mix(0.9, 0.5, tc);
  float bric = 1.0;
  vec3 colc = hsv2rgb(vec3(huec, satc, bric))+cli*vec3(0.9, 0.7, 0.9);
  tc *= tc;

  vec4 cc = vec4(colc*0.66, tc);
  cc = clamp(cc, 0.0, 1.0);

  return cc.xyz*cc.w;
}

vec3 stars(vec3 ro, vec3 rd, float cloudDensity) {
  vec3 col = vec3(0.0);
  vec3 srd = toSpherical(rd.xzy);
  
  const float m = LAYERS;

  for (float i = 0.0; i < m; ++i) {
    vec2 pp = srd.yz+0.5*i;
    float s = i/(m-1.0);
    vec2 dim  = vec2(mix(0.025, 0.003, s)*PI);
    vec2 np = mod2(pp, dim);
    vec2 h = hash2(np+127.0+i);
    vec2 o = -1.0+2.0*h;
    float y = sin(srd.y);
    pp += o*dim*0.5;
    pp.y *= y;
    float l = length(pp);
  
    float h1 = fract(h.x*109.0);
    float h2 = fract(h.x*113.0);
    float h3 = fract(h.x*127.0);

    vec3 hsv = vec3(fract(0.025-0.4*h1*h1), mix(0.5, 0.125, s), 1.0);
    vec3 scol = mix(8.0*h2, 0.25*h2*h2, s)*hsv2rgb(hsv);

    vec3 ccol = col+ exp(-(2000.0/mix(2.0, 0.25, s))*max(l-0.001, 0.0))*scol;
    float p = i < 3.0 ? mix(0.125, 2.0, cloudDensity)*y : y;
    p = clamp(p, 0.0, 1.0);
    col = h3 < p ? ccol : col;
  }
  
  return col;
}

vec3 grid(vec3 ro, vec3 rd) {
  vec3 srd = toSpherical(rd.xzy);
  
  const float m = 1.0;

  const vec2 dim = vec2(1.0/8.0*PI);
  vec2 pp = srd.yz;
  vec2 np = mod2(pp, dim);

  vec3 col = vec3(0.0);

  float y = sin(srd.y);
  float d = min(abs(pp.x), abs(pp.y*y));
  
  float aa = 2.0/RESOLUTION.y;
  
  col += 2.0*vec3(0.5, 0.5, 1.0)*exp(-2000.0*max(d-0.00025, 0.0));
  
  return 0.25*tanh(col);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 q = fragCoord.xy/RESOLUTION.xy; 
  vec2 p = -1.0 + 2.0*q;
  p.x *= RESOLUTION.x/RESOLUTION.y;

  vec3 ro = vec3(2.0, 0, 0.);
  ro.xy *= ROT(-0.33*sin(TTIME/12.0));
  ro.xz *= ROT(1.5+0.33*sin(TTIME/12.0));
  vec3 la = vec3(0.0, 0.0, 0.0);

  vec3 ww = normalize(la - ro);
  vec3 uu = normalize(cross( vec3(0.0,1.0,0.0), ww));
  vec3 vv = normalize(cross(ww,uu));

  const float rdd = 2.0;
  vec3 rd = normalize(p.x*uu + p.y*vv + rdd*ww);

  vec3 col = vec3(0.0);

  float cloudDensity;  
  col += clouds(ro, rd, cloudDensity);
  col += stars(ro, rd, cloudDensity);
  col += grid(ro, rd);
  
  col = clamp(col, 0.0, 1.0);
  fragColor = vec4(postProcess(col, q),1.0);
}

'''


class StStarryBackgroundWithNebula(GlslSource):
    name = 'st_starry_background_with_nebula'
    label = 'Starry background with nebula'
    description = 'License CC0: Starry background with nebula Created for another shader but thought the background could be useful to others so extracted it'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
