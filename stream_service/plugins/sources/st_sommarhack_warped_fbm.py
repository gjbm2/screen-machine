"""Sommarhack warped fbm: License CC0: Sommarhack warped fbm More warped FBM stuff + sommarhack phoenix

Source: https://www.shadertoy.com/view/stXXRs
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Sommarhack warped fbm
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Sommarhack warped fbm
//  More warped FBM stuff + sommarhack phoenix
#define PI              3.141592654
#define TAU             (2.0*PI)
#define TIME            iTime
#define RESOLUTION      iResolution
#define TTIME           (TIME*TAU)
#define DOT2(x)         dot(x, x)
#define ROT(a)          mat2(cos(a), sin(a), -sin(a), cos(a))
#define PCOS(x)         (0.5+0.5*cos(x))

vec2 g_qx = vec2(0.0);
vec2 g_qy = vec2(0.0);

vec2 g_rx = vec2(0.0);
vec2 g_ry = vec2(0.0);

// https://www.iquilezles.org/www/articles/smin/smin.htm
float pmin(float a, float b, float k) {
  float h = clamp( 0.5+0.5*(b-a)/k, 0.0, 1.0 );
  return mix( b, a, h ) - k*h*(1.0-h);
}

float pmax(float a, float b, float k) {
  return -pmin(-a, -b, k);
}

float tanh_approx(float x) {
//  return tanh(x);
  float x2 = x*x;
  return clamp(x*(27.0 + x2)/(27.0+9.0*x2), -1.0, 1.0);
}

float noise(vec2 p) {
  p /= pow(1.1 + 0.25*(0.5 + 0.5*sin(0.1*(p.x + p.y) + TTIME/10.0)), 1.15);
  float a = sin(p.x);
  float b = cos(p.y);
  float c = sin(p.x + p.y);
  float d = mix(a, b, c);
  return tanh_approx(d);

}

// https://iquilezles.org/www/articles/fbm/fbm.htm
float fbm(vec2 p) {    
  const float A = 0.57;
  const float F = 2.1;
  const int numOctaves = 4;
  const mat2 rots[numOctaves] = mat2[](F*ROT(0.0/float(numOctaves)), F*ROT(1.0/float(numOctaves)), F*ROT(2.0/float(numOctaves)), F*ROT(3.0/float(numOctaves)));

  float t = 0.0;
  float f = 1.0;
  float a = 1.0;
  for(int i = 0; i<numOctaves; ++i) {
    t += a*noise(f*p);
    p *= rots[i];
    a *= A;
  }

  return tanh_approx(0.3+t);
}

const float scale1 = 1.4;
const float scale2 = 0.2; 

void compute_globals(vec2 p) {
  const vec2 qx = vec2(1.0,3.0)*scale2;
  const vec2 qy = vec2(5.0,2.0)*scale2;

  const vec2 rx = vec2(2.0,9.0)*scale2;
  const vec2 ry = vec2(8.0,3.0)*scale2;
  
  g_qx = qx*ROT(TTIME/100.0);
  g_qy = qy*ROT(TTIME/90.0);

  g_rx = rx*ROT(TTIME/80.0);
  g_ry = ry*ROT(TTIME/70.0);
}

// https://iquilezles.org/www/articles/warp/warp.htm
vec3 warp(in vec2 p, float d) {
  float lp = length(p);
  p *= ROT(-TTIME/100.0 + 0.125*length(p));
  
  vec2 qx = g_qx;
  vec2 qy = g_qy;

  vec2 rx = g_rx;
  vec2 ry = g_ry;

  vec2 q = vec2(fbm(p + qx),
                fbm(p + qy));

  vec2 r = vec2(fbm(0.25*p + scale1*q + rx),
                fbm(0.5*p + scale1*q + ry));

  float f = fbm(0.75*p + scale1*r);
 
  const vec3 col1 = vec3(0.1, 0.3, 0.8);
  const vec3 col2 = vec3(0.7, 0.3, 0.5);

  float scaleIt = 1.5*(pow(abs((p.x + p.y)), 0.7));

  float pp= mix(0.7, 0.35, 1.5*tanh_approx(length(p)));

  float fi = tanh_approx(pow(scaleIt, pp)*d / (f - d*scaleIt));
  vec3 col =  abs(fi + 0.1)*(+0.3 + length(q)*col1 + length(r)*col2);
  return pow(col, vec3(2.0, 1.5, 1.5)*tanh_approx(0.25*lp));
}


float circle(vec2 p, float r) {
  return length(p) - r;
}

// Distance fields from: https://iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm
float vesica(vec2 p, vec2 sz) {
  if (sz.x < sz.y) {
    sz = sz.yx;
  } else {
    p  = p.yx; 
  }
  vec2 sz2 = sz*sz;
  float d  = (sz2.x-sz2.y)/(2.0*sz.y);
  float r  = sqrt(sz2.x+d*d);
  float b  = sz.x;
  p = abs(p);
  return ((p.y-b)*d>p.x*b) ? length(p-vec2(0.0,b))                         : length(p-vec2(-d,0.0))-r;
}

float moon(vec2 p, float d, float ra, float rb ) {
    p.y = abs(p.y);
    float a = (ra*ra - rb*rb + d*d)/(2.0*d);
    float b = sqrt(max(ra*ra-a*a,0.0));
    if( d*(p.x*b-p.y*a) > d*d*max(b-p.y,0.0) )
          return length(p-vec2(a,b));
    return max( (length(p          )-ra),
               -(length(p-vec2(d,0))-rb));
}

// Imprecise faster version
float fastMoon(vec2 p, float d, float ra, float rb) {
  float d0 = length(p) - ra;
  float d1 = length(p-vec2(d, 0.0)) - rb;
  return max(d0, -d1);
}

float roundedCross( in vec2 p, in float h )
{
    float k = 0.5*(h+1.0/h); // k should be const at modeling time
    p = abs(p);
    return ( p.x<1.0 && p.y<p.x*(k-h)+h ) ? 
             k-sqrt(DOT2(p-vec2(1,k)))  :
           sqrt(min(DOT2(p-vec2(0,h)),
                    DOT2(p-vec2(1,0))));
}

float box(vec2 p, vec2 b) {
    vec2 d = abs(p)-b;
    return length(max(d,0.0)) + min(max(d.x,d.y),0.0);
}
 
float plane(vec2 p, vec3 pp) {
  return dot(p, pp.xy) + pp.z;
}
 
float summerhack(vec2 p) {
  // As the moon shapes are mostly used for cut-outs I think there is less need for exact
  //  moon distance and therefore rely on fastMoon instead
  vec2 op = p;
  p.x = abs(p.x);
  vec2 p0 = p;
  p0 -= vec2(0.0, 0.385);
  float d0 = circle(p0, 0.605);
  vec2 p1 = p;
  p1 -= vec2(0.0, -0.375);  
  float d1 = vesica(p1, vec2(0.04, 0.569))-(mix(0.005, 0.035, smoothstep(0.1, 0.2, p1.y)));
  
  vec2 p2 = p;
  p2 -= vec2(0.0, 0.4);
  p2 = -p2.yx;
  float d2 = fastMoon(p2, 0.075, 0.33, 0.275);
  
  vec2 p3 = p;
  p3 -= vec2(0.0, 0.65);
  float d3 = circle(p3, 0.367);
  
  vec2 p4 = p;
  p4 -= vec2(0.0, 0.43);
  float d4 = circle(p4, 0.29);
  
  vec2 p5 = p;
  p5 -= vec2(-0.185, 0.12);
  float d5 = circle(p5, 0.30);
  
  vec2 p6 = p;
  p6 -= vec2(0.12, -0.19);
  p6 *= ROT(0.65);
  float d6 = vesica(p6, vec2(0.15, 0.024))-0.0175;
  
  vec2 p7 = p;
  p7 -= vec2(0.0, 0.735);
  p7 = -p7.yx;
  float d7 = fastMoon(p7, 0.13, 0.68, 0.595);
  
  vec2 p8 = p;
  p8 -= vec2(0.0, 0.7);
  p8 = -p8.yx;
  float d8 = fastMoon(p8, 0.1, 0.477, 0.4676);

  vec2 p9 = p;
  p9 -= vec2(0.25, 0.72);
  p9.x = -p9.x;
  float d9 = fastMoon(p9, 0.188, 0.73, 0.775);

  vec2 p10 = op;
  p10 -= vec2(0.0, 0.28);
  p10 = p10.yx;
  p10.x *= sign(op.x);
  p10.x += (-sign(op.x)+1.0)*-0.0775;
  float d10 = moon(p10, 0.045, 0.105, 0.095);

  vec2 p11 = p;
  p11 -= vec2(0.0, -0.78);
  p11 = p11.yx;
  float d11 = roundedCross(p11, 0.55);
  
  vec2 p12 = p;
  float d12 = plane(p12, vec3(normalize(vec2(-4.0, 1.0)), 0.315));
  
  vec2 p13 = p;
  p13 -= vec2(-0.05, -0.805);
  float d13 = circle(p13, 0.175);

  vec2 p14 = p;
  p14 -= vec2(0.0, -0.88);
  float d14 = p14.y;
  
  vec2 p15 = p;
  p15 -= vec2(0.45, -0.4);
  p15 = p15.yx;
  float d15 = fastMoon(p15, 0.14, 0.4, 0.4);

  vec2 p16 = op;
  p16 -= vec2(-0.095, 0.323);
  // Cheat to remove discontinuity in distance field due to hacking on d10
  float d16 = length(p16); 
    
  d11 = max(d11, -d13);
  d11 = max(d11, -d14);
  d11 = max(d11, -d15);
  d11 = pmax(d11, -d12, 0.0125);

  float dn = d3;
  dn = min(dn, d4);
  dn = min(dn, d2);
  dn = min(dn, d5);
  dn = min(dn, d6);
  dn = min(dn, d7);
  dn = min(dn, d8);
  dn = min(dn, d9);

  float d = d0;
  d = max(d, -dn);
  d = min(d, d1);
  d = min(d, d10);
  d = min(d, d11);
  d = min(d, d16);
  
  
  return d;
}

vec3 postProcess(vec3 col, vec2 q) {
  col = clamp(col, 0.0, 1.0);
  col = pow(col, vec3(1.0/2.2));
  col = col*0.6+0.4*col*col*(3.0-2.0*col);
  col = mix(col, vec3(dot(col, vec3(0.33))), -0.4);
  col *=0.5+0.5*pow(19.0*q.x*q.y*(1.0-q.x)*(1.0-q.y),0.7);
  return col;
}

vec3 effect(vec2 p, vec2 q) {
  compute_globals(p);
  float aa = 2.0 / RESOLUTION.y;
 
  vec3 col = vec3(0.0);  
  float d = summerhack(p);
  vec2 c = vec2(0.0, 0.385);
  
  const vec3 lcol2 = vec3(1.2, 1.3, 2.0)*1.5;
  col = warp(p*6.0, d-0.05);
  col = mix(2.0, 0.5, smoothstep(0.605, 1.5, length(p-c)))*col;
  col = col;

  col += lcol2*exp(-40.0*max(abs(d-mix(-0.05, -0.01, q.y)), 0.0));
  col = mix(col, vec3(1.0), smoothstep(-aa, aa, -d));
  col = mix(col, vec3(0.25*(lcol2*lcol2)*smoothstep(0.56, 0.8, q.y)), smoothstep(-aa, aa, -(d+aa*2.5)));
  return col.zyx;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord ) {
  vec2 q = fragCoord/RESOLUTION.xy;
  vec2 p = -1. + 2. * q;
  p.x *= RESOLUTION.x/RESOLUTION.y;
  
  vec3 col = effect(p, q);
  
  col = postProcess(col, q);
  fragColor = vec4(col, 1.0);
}
'''


class StSommarhackWarpedFbm(GlslSource):
    name = 'st_sommarhack_warped_fbm'
    label = 'Sommarhack warped fbm'
    description = 'License CC0: Sommarhack warped fbm More warped FBM stuff + sommarhack phoenix'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
