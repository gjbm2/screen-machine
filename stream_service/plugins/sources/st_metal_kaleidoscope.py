"""Metal Kaleidoscope: Licence CC0: Metal Kaleidoscope Experimenting with truchet, FBM, smooth kaleidoscope and metal like lighting

Source: https://www.shadertoy.com/view/3lVczV
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Licence CC0: Metal Kaleidoscope
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Licence CC0: Metal Kaleidoscope
// Experimenting with truchet, FBM, smooth kaleidoscope and metal like lighting

// SABS        from: ollj (shadertoy) (SABS is a really great tool)
// hsv2rgb     from: https://stackoverflow.com/questions/15095909/from-rgb-to-hsv-in-opengl-glsl
// modMirror1  from: http://mercury.sexy/hg_sdf/
// pmin        from: iq (shadertoy)
// tanh_approx from: some math site, don't remember

// fbm described by iq here: https://www.iquilezles.org/www/articles/fbm/fbm.htm

#define PI              3.141592654
#define TAU             (2.0*PI)
#define TIME            iTime
#define RESOLUTION      iResolution
 
#define LESS(a,b,c)     mix(a,b,step(0.,c))
#define SABS(x,k)       LESS((.5/(k))*(x)*(x)+(k)*.5,abs(x),abs(x)-(k))
#define ROT(a)          mat2(cos(a), sin(a), -sin(a), cos(a))

#define PERIOD          30.0
#define NPERIOD         floor(TIME/PERIOD)
#define TIMEINPERIOD    mod(TIME, PERIOD)
#define FADE            1.0

const float  truchet_lw = 0.05;
const mat2[] truchet_rots = mat2[](ROT(0.0*PI/2.0), ROT(1.00*PI/2.0), ROT(2.0*PI/2.0), ROT(3.0*PI/2.0));

float l2(vec2 p){
  return dot(p, p);
}

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
  float decay = 0.75/(1.0+0.125*l2(p));
  float d = df(p, h, n);
  const float ww = 0.085;
  float height = smoothstep(0.0, ww, d);
  return pmax(2.0*height*decay, 0.5, 0.25);
}

float fbm(vec2 p, float h) {
  const float aa = -0.45;
  const mat2  pp = 2.03*ROT(1.0);

  float a = 1.0;
  float d = 0.0;
  float height = 0.0;
  
  for (int i = 0; i < 4; ++i) {
    height += a*hf(p, h);
    d += a;
    a *= aa;
    p *= pp;
  }
  
  return height/d;
}

float height(vec2 p) {
  p.x = SABS(p.x, 0.1*abs(p.y)+0.001);
  float h = hash(NPERIOD);
  float tp = TIMEINPERIOD/PERIOD;
  p*=ROT(TIMEINPERIOD*0.075);
//  p*=ROT(-pow(l2(p), mix(0.25, 0.75, h)));
  p*=ROT(-PI*tanh_approx(0.125*(l2(p)-0.25)));
  
  p*=mix(1.5, 2.5, mix(tp, 1.0-tp, h));
  return fbm(p, h);
}

vec3 normal(vec2 p) {
  vec2 eps = vec2(4.0/RESOLUTION.y, 0.0);
  
  vec3 n;
  
  n.x = height(p - eps.xy) - height(p + eps.xy);
  n.y = 2.0*eps.x;
  n.z = height(p - eps.yx) - height(p + eps.yx);
  
  return normalize(n);
}

vec3 postProcess(vec3 col, vec2 q)  {
  col=pow(clamp(col,0.0,1.0),vec3(1.0/2.2)); 
  col=col*0.6+0.4*col*col*(3.0-2.0*col);  // contrast
  col=mix(col, vec3(dot(col, vec3(0.33))), -0.4);  // saturation
  col*=0.5+0.5*pow(19.0*q.x*q.y*(1.0-q.x)*(1.0-q.y),0.7);  // vigneting
  return col;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 q = fragCoord/RESOLUTION.xy;
  vec2 p = -1. + 2. * q;
  p.x *= RESOLUTION.x/RESOLUTION.y;

  float aa = 2.0/RESOLUTION.y;

  vec3 ld1 = normalize(vec3(1.0, 1.0, 1.0));
  vec3 ld2 = normalize(vec3(-1.0, 0.75, 1.0));
  vec3 e  = vec3(p.x, -1.0, p.y);

  float l = length(p);
  
  float h = height(p);
  vec3  n = normal(p);

  vec3 hsv = vec3(mix(0.6, 0.9, 0.5+ 0.5*sin(TIME*0.1-10.0*h*l+(p.x+p.y))), tanh_approx(0.5*h), tanh_approx(10.0*l*h+.1));
  hsv.yz = clamp(hsv.yz, 0.0, 1.0);
  vec3 baseCol1 = hsv2rgb(hsv);
  vec3 baseCol2 = sqrt(baseCol1.zyx);
 
  float diff1 = max(dot(n, ld1), 0.0);
  float diff2 = max(dot(n, ld2), 0.0);

  vec3 col = vec3(0.0);
  const float basePow = 1.5;
  col += 1.00*baseCol1*pow(diff1, 16.0*basePow);
  col += 0.10*baseCol1*pow(diff1, 04.0*basePow);
  col += 0.15*baseCol2*pow(diff2, 08.0*basePow);
  col += 0.02*baseCol2*pow(diff2, 02.0*basePow);
  
  col *= 8.0;
//  col = tanh(8.0*col);
  col = postProcess(col, q);
  
  float fadeIn  = smoothstep(0.0, FADE, TIMEINPERIOD);
  float fadeOut = 1.0-smoothstep(PERIOD-FADE, PERIOD, TIMEINPERIOD);
  col = mix(vec3(0.0), col, fadeIn*fadeIn*fadeOut*fadeOut);
  fragColor = vec4(col, 1.0);
}
'''


class StMetalKaleidoscope(GlslSource):
    name = 'st_metal_kaleidoscope'
    label = 'Metal Kaleidoscope'
    description = 'Licence CC0: Metal Kaleidoscope Experimenting with truchet, FBM, smooth kaleidoscope and metal like lighting'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
