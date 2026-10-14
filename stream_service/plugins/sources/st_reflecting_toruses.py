"""Reflecting toruses: CC0: Reflecting toruses Numerous examples on shadertoy already on how to do repeating toruses so nothing ground breaking. Part of an Amiga tribute demo...

Source: https://www.shadertoy.com/view/Ns2XWc
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // CC0: Reflecting toruses
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// CC0: Reflecting toruses
//  Numerous examples on shadertoy already on how to do repeating toruses so nothing ground breaking.
//  Part of an Amiga tribute demo released earlier. Every late Amiga demo had rotating toruses

// Repeats itself after 20 sec

#define PI              3.141592654
#define TAU             (2.0*PI)
#define TIME            iTime
#define RESOLUTION      iResolution
#define ROT(a)          mat2(cos(a), sin(a), -sin(a), cos(a))
#define MISS            1E6

#define BB_TOLERANCE            0.0001
#define BB_NORM_OFF             0.001
#define BB_MAX_RAY_LENGTH       15.0
#define BB_MAX_RAY_MARCHES      60
#define BB_MAX_SHADOW_MARCHES   15
#define BB_MAX_REFLECTIONS      3

const mat2 rot0            = ROT(0.0);
const vec3 std_gamma       = vec3(2.2);

const vec3 bb_lightPos     = 2.0*vec3(4.0, 3.0, 1.5);
const vec3 bb_backLightPos = bb_lightPos.x*vec3(-1.0, 1.0, -1.0);
const vec3 bb_skyCol1      = vec3(0.2, 0.4, 0.6);
const vec3 bb_skyCol2      = vec3(0.4, 0.7, 1.0);
const vec3 bb_sunCol       = vec3(8.0,7.0,6.0)/8.0;
const vec3 bb_sunDir       = normalize(bb_lightPos);
const float bb_period      = 20.0;

const float bb_bottom      = -.85;

vec3   bb_g_baseColor      = vec3(0.0);
float  bb_g_refFactor      = 0.0;

mat2   bb_g_rot            = rot0;
float  bb_g_fi             = 0.0;
float  bb_g_fo             = 0.0;
float  bb_g_fi13           = 0.0;
float  bb_g_fi23           = 0.0;


float saturate(float a) { return clamp(a, 0.0, 1.0); }

// IQ's smooth min: https://www.iquilezles.org/www/articles/smin/smin.htm
float pmin(float a, float b, float k) {
  float h = clamp( 0.5+0.5*(b-a)/k, 0.0, 1.0 );
  return mix( b, a, h ) - k*h*(1.0-h);
}

float pmax(float a, float b, float k) {
  return -pmin(-a, -b, k);
}

// IQ's box distance function: https://www.iquilezles.org/www/articles/distfunctions/distfunctions.htm
float box(vec3 p, vec3 b) {
  vec3 q = abs(p) - b;
  return length(max(q,0.0)) + min(max(q.x,max(q.y,q.z)),0.0);
}

// IQ's torus distance function: https://www.iquilezles.org/www/articles/distfunctions/distfunctions.htm
float torus(vec3 p, vec2 t) {
  vec2 q = vec2(length(p.xz)-t.x,p.y);
  return length(q)-t.y;
}

// From: http://mercury.sexy/hg_sdf/
float mod1(inout float p, float size) {
  float halfsize = size*0.5;
  float c = floor((p + halfsize)/size);
  p = mod(p + halfsize, size) - halfsize;
  return c;
}

vec2 mod2_1(inout vec2 p) {
  vec2 c = floor(p + 0.5);
  p = fract(p + 0.5) - 0.5;
  return c;
}

vec3 postProcess(vec3 col, vec2 q) {
  col = clamp(col, 0.0, 1.0);
  col = pow(col, 1.0/std_gamma);
  col = col*0.6+0.4*col*col*(3.0-2.0*col);
  col = mix(col, vec3(dot(col, vec3(0.33))), -0.4);
  col *=0.5+0.5*pow(19.0*q.x*q.y*(1.0-q.x)*(1.0-q.y),0.7);
  return col;
}

float bb_planeIntersect(vec3 ro, vec3 rd, float mint) {
  vec3 p = ro + rd*mint;
  return (bb_bottom-p.y)/rd.y;
}

vec3 bb_skyColor(vec3 rd) {
  float sunDot = max(dot(rd, bb_sunDir), 0.0);  
  vec3 final = vec3(0.);

  float roundBox = length(max(abs(rd.xz/max(0.0,rd.y))-vec2(0.5, 0.5),0.0))-0.1;
  final += vec3(0.75)* pow(saturate(1.0 - roundBox*0.5), 9.0);
  
  final += mix(bb_skyCol1, bb_skyCol2, rd.y);
  final += 0.5*bb_sunCol*pow(sunDot, 20.0);
  final += 4.0*bb_sunCol*pow(sunDot, 400.0);    
  return final;
}

float bb_df(vec3 p) {
  float fi   = bb_g_fi;
  float fo   = bb_g_fo;
  mat2  rot  = bb_g_rot;
  float fi13 = bb_g_fi13;
  float fi23 = bb_g_fi23;
  
  float oo = mix(2.5, 0.5, fi*fo);
  float ss = mix(0.05, 0.4, fi13*fo);
  float rr = mix(0.125, 0.75, fi23*fo);
  
  vec3 p0 = p;
  p0.y -= 0.5;
  p0.zy *= rot;
  p0.xy *= rot;
  float d0 = box(p0, vec3(0.65));
  
  vec3 p1 = p;
  p1.y -= oo;

  float s = 1.0;

  float d1 = torus(p1, s*vec2(1.0, 0.125));

  vec3 c1 = vec3(0.125);

  float trf = 0.75;
  
  for (int i = 0; i < 3; ++i) {
    p1.xz *= rot;
    p1.xyz = p1.zxy;

    float pr = length(p1.xy);
    float pa = atan(p1.y, p1.x);
  
    float n = mod1(pa, TAU/8.0);
    
    p1.xy = pr*vec2(cos(pa), sin(pa));
    p1.x -= s;
    s *= ss;
    float dd = torus(p1, s*vec2(1.0, rr));


    d1 = pmax(d1, -dd, 0.75*s);
    trf = dd < d1 ? trf = 1.0-trf : trf;
    d1 = min(d1, dd);
  }
  

  float rf = 0.35;
  vec3 bc = vec3(0.5);

  float d = d0;

  d = d0;    
  
  d = pmax(d, -d1, 0.1);

  if (d1 < d) {
    bc = c1;
    rf = trf;
    d = d1; 
  }

  bb_g_refFactor = rf;
  bb_g_baseColor = bc;

  return d;
}

vec3 bb_normal(vec3 pos) {
  vec3 eps = vec3(BB_NORM_OFF, 0.0, 0.0);
  vec3 nor;
  
  nor.x = bb_df(pos+eps.xyy) - bb_df(pos-eps.xyy);
  nor.y = bb_df(pos+eps.yxy) - bb_df(pos-eps.yxy);
  nor.z = bb_df(pos+eps.yyx) - bb_df(pos-eps.yyx);
  
  return normalize(nor);
}
 
float bb_rayMarch(vec3 ro, vec3 rd, float initial, out float nearest, out int iter) {
  float t = initial;

  float n = 1E6;
  int ii = 0;

  for (int i = 0; i < BB_MAX_RAY_MARCHES; ++i) {
    ii = i;
    vec3 p = ro + rd*t;
    
    float d = bb_df(p);
    n = min(n, d);
    
    if (d < BB_TOLERANCE || t >= BB_MAX_RAY_LENGTH) break;
    
    t += d;
  }
  
  iter = ii;
  nearest = n;
  
  return t < BB_MAX_RAY_LENGTH ? t : MISS;
}

float bb_softShadow(vec3 ps, vec3 ld, float mint, float k) {

  float res = 1.0;
  float t = mint*6.0;
  int mat;
  for (int i=0; i < BB_MAX_SHADOW_MARCHES; ++i) {
    vec3 p = ps + ld*t;
    float d = bb_df(p);
    res = min(res, k*d/t);
    if (res < BB_TOLERANCE) break;
    
    t += max(d, mint);
  }
  return clamp(res, 0.0, 1.0);
}

vec3 bb_render(vec3 ro, vec3 rd) { 
  vec3 finalCol = vec3(0.0);

  float aggRefFactor = 1.0;

  vec3 bg = bb_skyColor(rd);
  int titer = 0;
  int tref = 0;
  
  for (int rc = 0; rc < BB_MAX_REFLECTIONS; ++rc) {  
      if (aggRefFactor < 0.05) break;
  
      vec3 sky = bb_skyColor(rd);
  
      const float mint = 0.05;
      float tp = bb_planeIntersect(ro, rd, mint);

      int iter;
      float nearest;
      float tm = bb_rayMarch(ro, rd, mint, nearest, iter);
      titer += iter;
      ++tref;
      
      vec3 baseColor  = bb_g_baseColor;
      float refFactor = bb_g_refFactor;
      
      float shine = exp(-5.0*nearest);
      const float shinef = 0.125;
      const vec3 shineCol = vec3(1.25).zyx;
      shine *= shinef;

      if(tm >= MISS && tp <= 0.0) {
        // We hit the sky
        finalCol += aggRefFactor*mix(sky, shineCol, shine);
        break;
      }

      vec3 p = ro + tm*rd;
      vec3 nor = bb_normal(p);
      float fakeAo = 1.0 - smoothstep(0.5, 1.2, float(iter)/float(BB_MAX_RAY_MARCHES));
      
      vec3 pp = ro + tp*rd;
      vec2 pp1 = pp.xz;

      pp1.x -= -2.0*TIME*0.5;
      pp1 *= sqrt(0.5);
      vec2 np1 = mod2_1(pp1);
      
      if (tp < tm && tp >= 0.0) {
        // Hit plane
        p = pp;
        float dd = min(abs(pp1.x), abs(pp1.y));
        baseColor = vec3(0.75)-0.25*exp(-50.0*dd);
        refFactor = 0.8;
        nor = vec3(0.0, 1.0, 0.0);
        fakeAo = 1.0;
      }
      
      refFactor *= pow(abs(dot(nor, rd)), 0.25);
      vec3 ld  = normalize(bb_lightPos - p);
      vec3 bld = normalize(bb_backLightPos - p);
  
          
      float dif  = max(dot(nor, ld), 0.0);
      float bdif = max(dot(nor, bld), 0.0);
      float spe  = pow(max(dot(reflect(ld, nor), rd), 0.0), 40.0);
      float sha  = bb_softShadow(p, ld, 0.1, 4.0);
      vec3 col = 0.8*baseColor*mix(0.2, 1.0, dif*sha*fakeAo) + 0.25*spe;
      col += baseColor*mix(0.0, 0.2, bdif);
      col *= refFactor;

      float yy = 1.0-exp(-4.0*float(iter)/float(BB_MAX_RAY_MARCHES));
      
      col = mix(col, shineCol, max(shine, yy*shinef));
      // Very very random code
      col *= mix(0.95, -1.0, abs(dot(nor,rd)));

      finalCol += aggRefFactor*(col);

      aggRefFactor *= (1.0 - refFactor);
      
      ro = p;
      rd = reflect(rd, nor);
  }

  return finalCol;
}

vec3 bb_effect(vec2 p, vec2 q) {
  float gtime = TIME;
  float ltime = mod(gtime, bb_period);

  bb_g_rot    = ROT(TAU*TIME*0.75/4.0);
  bb_g_fi     = smoothstep(0.0, 1.0, ltime);
  bb_g_fo     = 1.0-smoothstep(bb_period-1.25, bb_period-0.25, ltime);
  bb_g_fi13   = smoothstep(bb_period*1.0/3.0-0.5, bb_period*1.0/3.0+0.5, ltime);
  bb_g_fi23   = smoothstep(bb_period*2.0/3.0-0.5, bb_period*2.0/3.0+0.5, ltime);
  
  vec3 ro = 0.6*vec3(6.0, 5.0, -2.0);
  vec3 up = vec3(0.0, 1.0, 0.0);

  ro.xz *= ROT(sin(TIME*sqrt(0.3)));

  vec3 la  = vec3(0.0);

  vec3 ww = normalize(la - ro);
  vec3 uu = normalize(cross(up, ww));
  vec3 vv = normalize(cross(ww,uu));
  vec3 rd = normalize(p.x*uu + p.y*vv + 2.5*ww);

  vec3 col = bb_render(ro, rd);

  return col;  
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 q = fragCoord/RESOLUTION.xy;
  vec2 p = -1. + 2. * q;
  p.x *= RESOLUTION.x/RESOLUTION.y;

  vec3 col = bb_effect(p, q);

  col = postProcess(col, q);

  fragColor = vec4(col, 1.0);
}
'''


class StReflectingToruses(GlslSource):
    name = 'st_reflecting_toruses'
    label = 'Reflecting toruses'
    description = 'CC0: Reflecting toruses Numerous examples on shadertoy already on how to do repeating toruses so nothing ground breaking. Part of an Amiga tribute demo...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
