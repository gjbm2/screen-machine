"""Lucky buggy galaxy: License CC0: Lucky buggy galaxy When trying to create a galaxy for the: once upon a time... space I mistakenly put in s when I meant sqrt(0.5) and it turned...

Source: https://www.shadertoy.com/view/tl2fzD
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Lucky buggy galaxy
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Lucky buggy galaxy
//  When trying to create a galaxy for the: once upon a time... space
//	I mistakenly put in s when I meant sqrt(0.5) and it turned out quite cool IMHO.
//  So I thought I share my mistake

#define PI          3.141592654
#define TAU         (2.0*PI)
#define TIME        iTime
#define RESOLUTION  iResolution

vec2 toPolar(vec2 p) {
  return vec2(length(p), atan(p.y, p.x));
}

vec2 toRect(vec2 p) {
  return p.x*vec2(cos(p.y), sin(p.y));
}

void rot(inout vec2 p, float a) {
  float c = cos(a);
  float s = sin(a);
  p = vec2(c*p.x + s*p.y, -s*p.x + c*p.y);
}

float noise1(vec2 p) {
  // Randomly coded "noise" function
  float a = cos(p.x);
  float b = cos(p.y);

  float c = cos(p.x*sqrt(3.5));
  float d = cos(p.y*sqrt(1.5));

  return a*b*c*d;
}

float galaxy(vec2 p, float a, float z) {
  // Apply angular distort, there are probably better ways to do this
  vec2 pp = toPolar(p);
  pp.y += pp.x*3.0 + a;
  p = toRect(pp);
  p *= z;
  return noise1(p);
}

float height(vec2 p) {
  // FBM with a twist (ie bug)
  float s = 0.0;
  float a = 1.0;
  float f = 15.0;
  float d = 0.0;
  rot(p, 0.075*TIME);
  for (int i = 0; i < 4; ++i) {
    float g = a*galaxy(p, 0.15*float(i) + -TIME*( + 0.1*float(i)), f);
    s += g;
    a *= 0.5;
    f *= s; // This is a bug that happens to look cool, intended to be sqrt(0.5) (boring), s much more fun
    d += a;
  }
  
  return 0.5 + 0.5*(s/d)*exp(-5.5*dot(p, p));
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
  col=pow(clamp(col,0.0,1.0),vec3(0.75)); 
  col=col*0.6+0.4*col*col*(3.0-2.0*col);
  col=mix(col, vec3(dot(col, vec3(0.33))), -0.4);
  col*=0.5+0.5*pow(19.0*q.x*q.y*(1.0-q.x)*(1.0-q.y),0.7);
  return col;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 q = fragCoord/RESOLUTION.xy;
  vec2 p = -1. + 2. * q;
  p.x *= RESOLUTION.x/RESOLUTION.y;
 
  vec3 lp1 = vec3(-2.0, 0.5, 2.0);
  vec3 ld1 = normalize(lp1 - vec3(p.x, 0.0, p.y));
  vec3 lp2 = vec3(2.0, 0.5, 2.0);
  vec3 ld2 = normalize(lp2 - vec3(p.x, 0.0, p.y));

  float h = height(p);
  float th = tanh(h);
  vec3 n = normal(p);
  
  float diff1 = max(dot(ld1, n), 0.0);
  float diff2 = max(dot(ld2, n), 0.0);

  vec3 col = vec3(0.0);
  col += vec3(0.5, 0.5, 0.75)*h;
  col += 0.5*pow(diff1, 10.0);
  col += 0.25*pow(diff2, 5.0);
  col += vec3(1.0)*pow(sin(PI/2.0*th), 5.0);

  col = postProcess(col, q);

  fragColor = vec4(col, 1.0);
}
'''


class StLuckyBuggyGalaxy(GlslSource):
    name = 'st_lucky_buggy_galaxy'
    label = 'Lucky buggy galaxy'
    description = 'License CC0: Lucky buggy galaxy When trying to create a galaxy for the: once upon a time... space I mistakenly put in s when I meant sqrt(0.5) and it turned...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
