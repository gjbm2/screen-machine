"""Sunday fractals: License CC0: Sunday fractals Inspired by: http://www.fractalforums.com/new-theories-and-research/very-simple-formula-for-fractal-patterns/ SABS from ollj

Source: https://www.shadertoy.com/view/Wtdfzr
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Sunday fractals
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Sunday fractals
// Inspired by: http://www.fractalforums.com/new-theories-and-research/very-simple-formula-for-fractal-patterns/
// SABS from ollj

#define RESOLUTION      iResolution
#define TIME            iTime
#define PI              3.141592654
#define TAU             (2.0*PI)
#define LESS(a,b,c)     mix(a,b,step(0.,c))
#define SABS(x,k)       LESS((.5/(k))*(x)*(x)+(k)*.5,abs(x),abs(x)-(k))
#define ROT(a)          mat2(cos(a), sin(a), -sin(a), cos(a))
#define L2(x)           dot(x,x)
#define PSIN(x)         (0.5+0.5*sin(x))
#define PERIOD          30.0
#define NPERIOD         floor(TIME/PERIOD)
#define TIMEINPERIOD    mod(TIME, PERIOD)
#define FADE            1.0
#define HPERIOD         hash(NPERIOD)

float hash(float co) {
  co += 2313.0;
  return fract(sin(co*12.9898) * 13758.5453);
}

float tanh_approx(float x) {
  return tanh(x);
  /*
  float x2 = x*x;
  return clamp(x*(27.0 + x2)/(27.0+9.0*x2), -1.0, 1.0);
  */
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

float height(vec2 p, vec2 c, float hh) {
  p*=mix(1.5, 0.5, TIMEINPERIOD/PERIOD);
  p+=mix(0.2, 0.0, TIMEINPERIOD/PERIOD);
  p*=ROT(TIMEINPERIOD*0.05);
  vec2 op = p;
  float l2 = L2(p);
  float rep = 2.0*round(mix(3.0, 30.0, hh*hh));
  float s = 0.05*6.0/rep;
  float n = smoothKaleidoscope(p, s, rep);
  p *= ROT(TAU*hh);
  vec2 u = p;

  float a = 0.0;

  
  for (int i = 0; i < 13; ++i) {
    float m = dot(u, u);
    u = SABS(u, 0.025)/m + c;
  }
  
  float h = L2(u-p);
  h = log(h-hh);
  h = abs(h);
  h = tanh_approx(.45*h*h+0.125/(l2+0.125));
  
  return h;
}

vec3 normal(vec2 p, vec2 c, float hh) {
  vec2 v;
  vec2 w;
  vec2 e = vec2(4.0/RESOLUTION.y, 0);
  
  vec3 n;
  n.x = height(p + e.xy, c, hh) - height(p - e.xy, c, hh);
  n.y = 2.0*e.x;
  n.z = height(p + e.yx, c, hh) - height(p - e.yx, c, hh);
  
  return normalize(n);
}

vec3 hsv2rgb(vec3 c) {
  const vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
  vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
  return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

vec3 postProcess(vec3 col, vec2 q)  {
  col=pow(clamp(col,0.0,1.0),vec3(1.0/2.2)); 
  col=col*0.6+0.4*col*col*(3.0-2.0*col);  // contrast
  col=mix(col, vec3(dot(col, vec3(0.33))), -0.4);  // saturation
  col*=0.5+0.5*pow(19.0*q.x*q.y*(1.0-q.x)*(1.0-q.y),0.7);  // vigneting
  return col;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  const float s = 1.25;
  const vec3 lp1 = s*vec3(1.0, 2.25, 1.0)*vec3(s, 1.0, s);
  const vec3 lp2 = s*vec3(-1.0, 2.25, 1.0)*vec3(s, 1.0, s);
  
  vec2 q = fragCoord/RESOLUTION.xy;
  vec2 p = -1. + 2. * q;
  p.x *= RESOLUTION.x/RESOLUTION.y;

  float hh = HPERIOD;

  vec3 col = vec3(0.0);
  vec2  c = vec2(-0.4-0.2*fract(7.0*hh), -0.4-0.2*fract(13.0*hh));

  float h = height(p, c, hh);
  vec3  n = normal(p, c, hh);

  vec3 ro = vec3(0.0, -10.0, 0.0);
  vec3 pp = vec3(p.x, 0.0, p.y);

  vec3 po = vec3(p.x, h, p.y);
  vec3 rd = normalize(po - ro);

  vec3 ld1 = normalize(lp1 - po);
  vec3 ld2 = normalize(lp2 - po);

  float l = length(p);

  vec3 hsv1 = vec3(hh+mix(0.0, 0.2, PSIN(0.3*TIME-2.0*l+(p.x+p.y))), tanh_approx(h*h), tanh_approx(3.0*h*h));
  hsv1.yz = clamp(hsv1.yz, 0.0, 1.0);
  vec3 col1 = hsv2rgb(hsv1);
  vec3 col2 = pow(col1, vec3(0.25, 0.5, 2.0));

  float diff1 = max(dot(n, ld1), 0.0);
  float diff2 = max(dot(n, ld2), 0.0);

  vec3  ref   = reflect(rd, n);
  float ref1  = max(dot(ref, ld1), 0.0);
  float ref2  = max(dot(ref, ld2), 0.0);

  col += col1*mix(0.1, 1.0, pow(diff1, 8.0))*0.5;
  col += col2*mix(0.1, 1.0, pow(diff2, 8.0))*0.5;
  col = pow(col, vec3(1.5));
  col += 8.0*col1*pow(ref1, 40.0);
  col += 4.0*col2*pow(ref2, 10.0);

  col = tanh(2.5*col);
  col = postProcess(col, q);
  
  float fadeIn  = smoothstep(0.0, FADE, TIMEINPERIOD);
  float fadeOut = 1.0-smoothstep(PERIOD-FADE, PERIOD, TIMEINPERIOD);
  col = mix(vec3(0.0), col, fadeIn*fadeIn*fadeOut*fadeOut);
  
  fragColor = vec4(col, 1.0);
}
'''


class StSundayFractals(GlslSource):
    name = 'st_sunday_fractals'
    label = 'Sunday fractals'
    description = 'License CC0: Sunday fractals Inspired by: http://www.fractalforums.com/new-theories-and-research/very-simple-formula-for-fractal-patterns/ SABS from ollj'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
