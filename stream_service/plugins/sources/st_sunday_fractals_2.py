"""Sunday fractals 2: License CC0: Sunday Fractals 2 Inspired by: http://www.fractalforums.com/new-theories-and-research/very-simple-formula-for-fractal-patterns/ SABS from ollj

Source: https://www.shadertoy.com/view/ttVBDw
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Sunday Fractals 2
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Sunday Fractals 2
// Inspired by: http://www.fractalforums.com/new-theories-and-research/very-simple-formula-for-fractal-patterns/
// SABS from ollj

#define RESOLUTION      iResolution
#define TIME            iTime
#define PI              3.141592654
#define TAU             (2.0*PI)
#define LESS(a,b,c)     mix(a,b,step(0.,c))
#define SABS(x,k)       LESS((.5/(k))*(x)*(x)+(k)*.5,abs(x),abs(x)-(k))
#define L2(x)           dot(x,x)
#define ROT(a)          mat2(cos(a), sin(a), -sin(a), cos(a))

float hash(float co) {
  co += 1234.;
  return fract(sin(dot(co, 12.9898)) * 13758.5453);
}

float maxComp(vec3 c) {
  return max(c.x, max(c.y, c.z));
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

float pmin(float a, float b, float k) {
  float h = clamp(0.5+0.5*(b-a)/k, 0.0, 1.0);
  return mix(b, a, h) - k*h*(1.0-h);
}

float pmax(float a, float b, float k) {
  return -pmin(-a, -b, k);
}

float pabs(float a, float k) {
  return pmax(a, -a, k);
}

vec2 toPolar(vec2 p) {
  return vec2(length(p), atan(p.y, p.x));
}

vec2 toRect(vec2 p) {
  return vec2(p.x*cos(p.y), p.x*sin(p.y));
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
  float rn = modMirror1(hpp.y, TAU/rep);

  float sa = PI/rep - SABS(PI/rep - abs(hpp.y), sm);
  hpp.y = sign(hpp.y)*(sa);

  hp = toRect(hpp);

  p = hp;

  return rn;
}

float fractal(vec2 p, vec2 c, vec2 ot) {
  vec2 u = p;
  float lx = 1E6;
  float ly = 1E6;
  float lp = 1E6;
  const int maxi = 9;
  float s = 1.0;

  for (int i = 0; i < maxi; ++i) {
    float m = dot(u, u);
    u = SABS(u, 0.075)/m + c;
    s *= m;
    float dx = abs(u.x - ot.x);
    float dy = abs(u.y - ot.y);
    float dp = abs(1.65-length(u));
    if(m > 0.033) {
      lx = min(lx, dx);
      ly = min(ly, dy);
    }
    lp = min(lp, dp);
  }
  
  float l = lp;
  l = pmin(l, lx, 0.05);
  l = pmin(l, ly, 0.05);
  l -= 0.025;
  return l*s;
}

float df(vec2 p, vec2 c, float hh) {
  p *= ROT(TIME*TAU/120.0);
  float rep = 2.0*round(mix(3.0, 12.0, hh));
  float sm = 0.025*10.0/rep;
  smoothKaleidoscope(p, sm, rep);
  p *= ROT(hh*TAU+0.05);
  vec2 u = p;
  vec2 ot = mix(1.5, -1.5, hh)*vec2(cos(TAU*hh*sqrt(0.5)), sin(TAU*hh));
  return fractal(p, c, ot);
}

vec3 postProcess(vec3 col, vec2 q) {
  col = clamp(col, 0.0, 1.0);
  col = pow(col, 1.0/vec3(2.2));
  col = col*0.6+0.4*col*col*(3.0-2.0*col);
  col = mix(col, vec3(dot(col, vec3(0.33))), -0.4);
  col *=0.5+0.5*pow(19.0*q.x*q.y*(1.0-q.x)*(1.0-q.y),0.7);
  return col;
}

vec3 color(vec2 p, vec2 c, float hh, float aa) {
  float d = df(p, c, hh);
  vec3 col = vec3(0.0);
  float l2 = tanh_approx(L2(p));
  vec3 hsv = vec3(0.0+hh+l2*0.5, mix(0.5, 0.75, l2), 1.0);
  vec3 glowCol = hsv2rgb(hsv)*2.0;
//  vec3 glowCol = vec3(0.5, 0.5, 1.0)*mix(2.0, 2.0, l2);
  glowCol = d < 0.0 ? glowCol : glowCol.zxy;
  col += glowCol*exp(-mix(300.0, 900.0, l2)*max(abs(d), 0.0));
  col += glowCol*abs(tanh_approx(d));
//  col = mix(col, vec3(1.0), smoothstep(-aa, aa, -d));
  return col;
}

void mainImage(out vec4 fragColor, vec2 fragCoord) {
  vec2 q = fragCoord/RESOLUTION.xy;
  vec2 p = -1. + 2. * q;
  p.x *= RESOLUTION.x/RESOLUTION.y;
  float aa = 2.0/RESOLUTION.y;
  const float period = 10.0;
  float nperiod = floor(TIME/period);
  float tperiod = mod(TIME, period);
  float hh = hash(nperiod);
  vec2  c = vec2(-1.30,  -1.30)+0.2*vec2(sin(0.05*TIME+TAU*hh*vec2(1.0, sqrt(0.5)*0.5)));
  
  vec2 o1 = vec2(1.0/8.0, 3.0/8.0)*aa;
  vec2 o2 = vec2(-3.0/8.0, 1.0/8.0)*aa;
  
  vec3 col = color(p+o1, c, hh, aa);
  float mc = maxComp(clamp(col, 0.0, 1.0));
  float dmc = length(vec2(dFdx(mc), dFdy(mc)))/(mc+0.075);
  if (dmc > 0.5) {
    col += color(p-o1, c, hh, aa);
    col += color(p+o2, c, hh, aa);
    col += color(p-o2, c, hh, aa);
    col *= 0.25;
//    col += vec3(1.0, 0.0, 0.0);
  }
  col = clamp(col, 0.0, 1.0);
  col *= smoothstep(0.0, 0.5, tperiod);
  col *= 1.0-smoothstep(period-0.5, period, tperiod);
  col = postProcess(col, q);
  fragColor = vec4(col, 1.0);
}
'''


class StSundayFractals2(GlslSource):
    name = 'st_sunday_fractals_2'
    label = 'Sunday fractals 2'
    description = 'License CC0: Sunday Fractals 2 Inspired by: http://www.fractalforums.com/new-theories-and-research/very-simple-formula-for-fractal-patterns/ SABS from ollj'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
