"""Smooth kaleidoscope: License CC0: Smooth kaleidoscope Kaleidoscope with smoothing I thought this might be useful to someone else so I shared this ugly barebones shared

Source: https://www.shadertoy.com/view/wtSBWh
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Smooth kaleidoscope
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Smooth kaleidoscope
// Kaleidoscope effects are cool but while the mirror effect ensure the curves are
//  continuous the derivates are not. 
//  SABS has been a great tool whenever I needed an abs function that gave the illusion
//   continuous derivates.
//  I patched SABS into a kaleidoscope effect to get a smooth kaleidoscopic effect.

// I thought this might be useful to someone else so I shared this ugly barebones shared

#define TIME       iTime
#define RESOLUTION iResolution

#define PI         3.141592654
#define TAU        (2.0*PI)

// SABS by ollj, this has turned out extremely useful on many occassions
//  https://www.shadertoy.com/view/Ws2SDK
#define LESS(a,b,c) mix(a,b,step(0.,c))
#define SABS(x,k)   LESS((.5/(k))*(x)*(x)+(k)*.5,abs(x),abs(x)-(k))

// http://mercury.sexy/hg_sdf/
float modMirror1(inout float p, float size) {
  float halfsize = size*0.5;
  float c = floor((p + halfsize)/size);
  p = mod(p + halfsize,size) - halfsize;
  p *= mod(c, 2.0)*2.0 - 1.0;
  return c;
}

void rot(inout vec2 p, float a) {
  float c = cos(a);
  float s = sin(a);
  p = vec2(c*p.x + s*p.y, -s*p.x + c*p.y);
}

vec2 toPolar(vec2 p) {
  return vec2(length(p), atan(p.y, p.x));
}

vec2 toRect(vec2 p) {
  return p.x*vec2(cos(p.y), sin(p.y));
}

// https://www.iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm
float box(vec2 p, vec2 b) {
  vec2 d = abs(p)-b;
  return length(max(d,0.0)) + min(max(d.x,d.y),0.0);
}

float df(vec2 p) {
  const float rep = 6.0;

  vec2 hp = p;

  vec2 hpp = toPolar(hp);
  
  // mod the angular component to get an kaleidoscope effect
  // modMirror ensure curves are continuous
  float rn = modMirror1(hpp.y, TAU/rep);

  // Apply smoothing on right side of screen to make derivate of curves continuous
  //   Left side non-smoothed for comparison
  if (p.x > 0.0) {
    float sa = PI/rep - SABS(PI/rep - abs(hpp.y), 0.25);
    hpp.y = sign(hpp.y)*sa;
  }

  hp = toRect(hpp);
  hp.x -= 0.5;
  
  rot(hp, TIME);
  float d1 = box(hp, 0.25*vec2(0.5, 0.75));

  float d = d1;

  d = abs(d) - 0.01;
  
  return d;
}

void mainImage(out vec4 fragColor, vec2 fragCoord) {
  vec2 q = fragCoord/RESOLUTION.xy;
  vec2 p = -1. + 2. * q;
  p.x *= RESOLUTION.x/RESOLUTION.y;
  
  float d = df(p);
  
  vec3 col = vec3(0.0);
  float aa = 1.0/RESOLUTION.y;
  col = mix(col, vec3(1.0), smoothstep(-aa, aa, -d));
  col += vec3(0.0, 0.5, 0.5)*(0.5 + 0.5*sin(200.0*d));
  fragColor = vec4(col, 1.0);
}
'''


class StSmoothKaleidoscope(GlslSource):
    name = 'st_smooth_kaleidoscope'
    label = 'Smooth kaleidoscope'
    description = 'License CC0: Smooth kaleidoscope Kaleidoscope with smoothing I thought this might be useful to someone else so I shared this ugly barebones shared'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
