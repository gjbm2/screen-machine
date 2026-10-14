"""Metal Kaleidoscope 2: Licence CC0: Metal Kaleidoscope 2 Experimenting with truchet, FBM, smooth kaleidoscope and metal like lighting take 2

Source: https://www.shadertoy.com/view/ttVyRc
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Licence CC0: Metal Kaleidoscope 2
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Licence CC0: Metal Kaleidoscope 2
// Experimenting with truchet, FBM, smooth kaleidoscope and metal like lighting take 2

// SABS        from: ollj (shadertoy) (SABS is a really great tool)
// hsv2rgb     from: https://stackoverflow.com/questions/15095909/from-rgb-to-hsv-in-opengl-glsl
// modMirror1  from: http://mercury.sexy/hg_sdf/
// pmin        from: iq (shadertoy)
// raySphere   from: iq (shadertoy)
// tanh_approx from: some math site, don't remember
// pmin        from: iq (shadertoy)

// fbm described by iq here: https://www.iquilezles.org/www/articles/fbm/fbm.htm

#define PI              3.141592654
#define TAU             (2.0*PI)
#define TIME            iTime
#define RESOLUTION      iResolution
#define PSIN(x)         (0.5+0.5*sin(x))
#define LESS(a,b,c)     mix(a,b,step(0.,c))
#define SABS(x,k)       LESS((.5/(k))*(x)*(x)+(k)*.5,abs(x),abs(x)-(k))
#define ROT(a)          mat2(cos(a), sin(a), -sin(a), cos(a))
#define L2(x)           dot(x, x)

#define PERIOD          30.0
#define NPERIOD         floor(TIME/PERIOD)
#define TIMEINPERIOD    mod(TIME, PERIOD)
#define FADE            1.0
#define HPERIOD         hash(NPERIOD)

const float  truchet_lw = 0.05;
const mat2[] truchet_rots = mat2[](ROT(0.0*PI/2.0), ROT(1.00*PI/2.0), ROT(2.0*PI/2.0), ROT(3.0*PI/2.0));

vec3 hsv2rgb(vec3 c) {
  const vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
  vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
  return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

float tanh_approx(float x) {
//  return tanh(x);
  float x2 = x*x;
  return clamp(x*(27.0 + x2)/(27.0+9.0*x2), -1.0, 1.0);
}

float hex(vec2 p, float r) {
  const vec3 k = vec3(-sqrt(3.0)/2.0,1.0/2.0,sqrt(3.0)/3.0);
  p = p.yx;
  p = abs(p);
  p -= 2.0*min(dot(k.xy,p),0.0)*k.xy;
  p -= vec2(clamp(p.x, -k.z*r, k.z*r), r);
  return length(p)*sign(p.y);
}

float circle(vec2 p, float r) {
  return length(p) - r;
}

float hash(float co) {
  co += 100.0;
  return fract(sin(co*12.9898) * 13758.5453);
}

float hash(vec3 co) {
  co += 100.0;
  return fract(sin(dot(co, vec3(12.9898,58.233, 12.9898+58.233))) * 13758.5453);
}

vec2 toPolar(vec2 p) {
  return vec2(length(p), atan(p.y, p.x));
}

vec2 toRect(vec2 p) {
  return vec2(p.x*cos(p.y), p.x*sin(p.y));
}

vec2 mod2_1(inout vec2 p) {
  vec2 c = floor(p + 0.5);
  p = fract(p + 0.5) - 0.5;
  return c;
}

float modMirror1(inout float p, float size) {
  float halfsize = size*0.5;
  float c = floor((p + halfsize)/size);
  p = mod(p + halfsize,size) - halfsize;
  p *= mod(c, 2.0)*2.0 - 1.0;
  return c;
}

float smoothKaleidoscope(inout vec2 p, float sm, float rep) {
  vec2 hp = p;

  vec2 hpp = toPolar(hp);
  const float ts = 2.5;
  hpp.x = tanh_approx(hpp.x/ts)*ts;
  float rn = modMirror1(hpp.y, TAU/rep);

  float sa = PI/rep - SABS(PI/rep - abs(hpp.y), sm);
  hpp.y = sign(hpp.y)*(sa);

  hp = toRect(hpp);

  p = hp;

  return rn;
}

float pmin(float a, float b, float k) {
  float h = clamp(0.5+0.5*(b-a)/k, 0.0, 1.0);
  return mix(b, a, h) - k*h*(1.0-h);
}

float pmax(float a, float b, float k) {
  return -pmin(-a, -b, k);
}

float truchet_cell0(vec2 p) {
  float d0  = circle(p-vec2(0.5), 0.5);
  float d1  = circle(p+vec2(0.5), 0.5);

  float d = 1E6;
  d = min(d, d0);
  d = min(d, d1);
  return d;
}

float truchet_cell1(vec2 p) {
  float d0  = abs(p.x);
  float d1  = abs(p.y);
  float d2 = circle(p, 0.25);

  float d = 1E6;
  d = min(d, d0);
  d = min(d, d1);
  d = min(d, d2);
  return d;
}

vec2 truchet(vec2 p, float h, out vec3 n) {
  float hd = circle(p, 0.4);

  vec2 hp = p;
  float rep = 2.0*floor(mix(5.0, 25.0, fract(h*13.0)));
  float sm = mix(0.05, 0.125, fract(h*17.0))*24.0/rep;
  float kn = 0.0;
  kn = smoothKaleidoscope(hp, sm, rep);
  hp *= ROT(0.02*TIME);
  hp += TIME*0.05;
  vec2 hn = mod2_1(hp);
  float r = hash(vec3(hn, h));
  hp *= truchet_rots[int(r*4.0)];

  float cd0 = truchet_cell0(hp);
  float cd1 = truchet_cell1(hp);
  float d0 = mix(cd0, cd1, (fract(r*13.0) > 0.5));

  float d = 1E6;
  d = min(d, d0);
  d = abs(d) - truchet_lw;

  n = vec3(hn, kn);

  return vec2(hd, d);
}

float df(vec2 p, float h, out vec3 n) {
  vec2 d = truchet(p, h, n); 
  return d.y;
}

float hf(vec2 p, float h) {
  vec3 n;
  float decay = 0.75/(1.0+0.125*L2(p));
  float d = df(p, h, n);
  const float ww = 0.085;
  float height = (smoothstep(0.0, ww, d));
  return pmax(2.0*height*decay, 0.5, 0.25);
}

float fbm(vec2 p, float h) {
  const float aa = -0.45;
  const mat2  pp = (2.03-0.0)*ROT(1.0);

  float a = 1.0;
  float d = 0.0;
  float height = 0.0;
  
  for (int i = 0; i < 3; ++i) {
    height += a*hf(p, h);
    d += a;
    a *= aa;
    p *= pp;
  }
  
  return height/d;
}

vec2 distortCoords(vec2 p, float h) {
  p.x = SABS(p.x, 0.1*abs(p.y)+0.001);
  float tp = TIMEINPERIOD/PERIOD;
  p*=ROT(TIMEINPERIOD*0.075);
  p*=ROT(-PI*tanh_approx(0.125*(L2(p)-0.25)));
  
  p*=mix(1.5, 2.5, mix(tp, 1.0-tp, h));
  return p;
}
const float exclusionRadius = 0.25;

float distanceField(vec2 p, float h, out vec3 n) {
  float c = circle(p, exclusionRadius);
  p = distortCoords(p, h);
//  return df(p, h, n);
  return pmin(c, df(p, h, n), 0.25);
}

float height(vec2 p, float h) {
  p = distortCoords(p, h);
  return tanh_approx(fbm(p, h));
}

vec3 normal(vec2 p, float h) {
  vec2 eps = vec2(4.0/RESOLUTION.y, 0.0);
  
  vec3 n;
  
  n.x = height(p - eps.xy, h) - height(p + eps.xy, h);
  n.y = 2.0*eps.x;
  n.z = height(p - eps.yx, h) - height(p + eps.yx, h);
  
  return normalize(n);
}

vec3 postProcess(vec3 col, vec2 q)  {
  col=pow(clamp(col,0.0,1.0),vec3(1.0/2.2)); 
  col=col*0.6+0.4*col*col*(3.0-2.0*col);  // contrast
  col=mix(col, vec3(dot(col, vec3(0.33))), -0.4);  // saturation
  col*=0.5+0.5*pow(19.0*q.x*q.y*(1.0-q.x)*(1.0-q.y),0.7);  // vigneting
  return col;
}

vec2 raySphere(vec3 ro, vec3 rd, vec4 sphere) {
  vec3 ce = sphere.xyz;
  float ra = sphere.w;
  vec3 oc = ro - ce;
  float b = dot(oc, rd);
  float c = dot(oc, oc) - ra*ra;
  float h = b*b - c;
  if (h<0.0) return vec2(-1.0); // no intersection
  h = sqrt(h);
  return vec2(-b-h, -b+h);
}

const vec3 up  = vec3(0.0, 1.0, 0.0);
const vec3 lp1 = 1.0*vec3(1.0, 1.25, 1.0);
const vec3 lp2 = 1.0*vec3(-1.0, 2.5, 1.0);

vec4 sphereCol(vec3 ro, vec3 rd, float h) {
  vec4 sphere = vec4(0.0, 0.0, 0.0, exclusionRadius*0.95);
  vec2 sphereIntersect = raySphere(ro, rd, sphere);
  if (sphereIntersect.x == -1.0) {
    return vec4(0.0);
  }

  vec3 p = ro + rd*sphereIntersect.x;
  vec3 n = normalize(p - sphere.xyz);
  vec3 r = reflect(rd, n);

  vec3 ld1 = normalize(lp1 - p);
  vec3 ld2 = normalize(lp2 - p);
  
  float diff1 = max(dot(n, ld1), 0.0);
  float diff2 = max(dot(n, ld2), 0.0);
  float ref1  = max(dot(r, ld1), 0.0);
  float ref2  = max(dot(r, ld2), 0.0);
  
  vec3 hsv = vec3(h+mix(0.6, 0.9, PSIN(TIME*0.1)), 0.5, 1.0);
  
  vec3 baseCol1 = hsv2rgb(hsv);
  vec3 baseCol2 = sqrt(baseCol1.zyx);
  vec3 matCol   = 1.0-baseCol1*baseCol2;

  float fres = dot(-n, rd);
  
  vec3 col = vec3(0.0);
  col += baseCol1*pow(diff1, 5.0);
  col += baseCol2*pow(diff2, 2.5);
  col *= 0.125;
  
  col += 0.5*baseCol1*pow(ref1, 10.0);
  col += 0.5*baseCol2*pow(ref2, 5.0);
  
//  col += vec3(pow(1.0-fres, 6));
  float t = smoothstep(0.0, 0.25, fres);
  
  return vec4(col, t);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 q = fragCoord/RESOLUTION.xy;
  vec2 p = -1. + 2. * q;
  p.x *= RESOLUTION.x/RESOLUTION.y;

  float aa = 2.0/RESOLUTION.y;

  float hh = HPERIOD;
  float l  = length(p);
  
  float fakeNorthernLightPhase = L2(p.x)*10.0;
  float fakeNorthernLightAmp = mix(2.0, 10.0, tanh_approx(0.25*abs(p.x)));
  float eyeHeight = mix(10.0, mix(10.0, fakeNorthernLightAmp, sqrt(q.y)), PSIN(fakeNorthernLightPhase));

  
  vec3  dn;
  float d  = distanceField(p, hh, dn);
  float h  = height(p, hh);
  vec3  n  = normal(p, hh);

  vec3 ro = vec3(0.0, eyeHeight, 0.0);
  vec3 pp = vec3(p.x, 0.0, p.y);

  vec3 po = vec3(p.x, h, p.y);
  vec3 rd = normalize(po - ro);
  vec3 prd = normalize(pp - ro);
  

  vec3 ld1 = normalize(lp1 - po);
  vec3 ld2 = normalize(lp2 - po);

  vec3 hsv = vec3(hh+mix(0.6, 0.9, PSIN(TIME*0.1-10.0*l+(p.x+p.y))), tanh_approx(h*h*1.0), tanh_approx(1.0*h+.1));
  hsv.yz = clamp(hsv.yz, 0.0, 1.0);
  vec3 baseCol1 = hsv2rgb(hsv);
  vec3 baseCol2 = sqrt(baseCol1.zyx);
  vec3 matCol   = 1.0-baseCol1*baseCol2;
 
  float diff1 = max(dot(n, ld1), 0.0);
  float diff2 = max(dot(n, ld2), 0.0);

  vec3  ref   = reflect(rd, n);
  float ref1  = max(dot(ref, ld1), 0.0);
  float ref2  = max(dot(ref, ld2), 0.0);

  baseCol1 *= mix(0.0, 4.0, 1.0/L2(lp1 - po));
  baseCol2 *= mix(0.0, 3.0, 1.0/L2(lp2 - po));

  vec3 col = vec3(0.0);
  const float basePow = 1.25;
  col += 1.00*matCol*baseCol1*mix(0.1, 1.0, pow(diff1, 4.0))*0.5;
  col += 0.50*matCol*baseCol2*mix(0.1, 1.0, pow(diff2, 2.0))*0.5;
  col = pow(col, vec3(1.25));
  col += 4.0*baseCol1*pow(ref1, 20.0);
  col += 2.0*baseCol2*pow(ref2, 10.0);

  float gd = d;
  const float glow_lw = 0.025;
  gd = abs(gd)-glow_lw*2.0;
  gd = abs(gd)-glow_lw;
  vec3 glowCol = vec3(1.0);
  glowCol = mix(baseCol1, glowCol, max(dot(ld1, up), 0.0));
  glowCol = mix(baseCol2, glowCol, max(dot(ld2, up), 0.0));
  vec3 finalGlowCol = glowCol*exp(-20.0*max(gd, 0.0));
  finalGlowCol = mix(finalGlowCol, glowCol, smoothstep(-aa, aa, -d+glow_lw));
  
  float tuneOut = sqrt(1.0-q.x);
  
  col = clamp(col, 0.0, 1.0);
  col = mix(col, vec3(0.0), smoothstep(-aa, aa, -d));

  vec4 scol = sphereCol(ro, prd, hh);
  col = mix(col, scol.xyz, scol.w);  
  col -= 0.5*0.125*tuneOut*finalGlowCol;
  
  col = postProcess(col, q);
  
  float fadeIn  = smoothstep(0.0, FADE, TIMEINPERIOD);
  float fadeOut = 1.0-smoothstep(PERIOD-FADE, PERIOD, TIMEINPERIOD);
  col = mix(vec3(0.0), col, fadeIn*fadeIn*fadeOut*fadeOut);
  fragColor = vec4(col, 1.0);
}

'''


class StMetalKaleidoscope2(GlslSource):
    name = 'st_metal_kaleidoscope_2'
    label = 'Metal Kaleidoscope 2'
    description = 'Licence CC0: Metal Kaleidoscope 2 Experimenting with truchet, FBM, smooth kaleidoscope and metal like lighting take 2'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
