"""Icecream warped FBM: License: CC0 Result after playing around with warped FBMs for a few hours Based on: https://iquilezles.org/www/articles/warp/warp.htm

Source: https://www.shadertoy.com/view/td2yzR
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License: CC0
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License: CC0
//   Result after playing around with warped FBMs for a few hours
//   Based on: https://iquilezles.org/www/articles/warp/warp.htm

#define PI    3.141592654
#define TAU   (2.0*PI)
#define TTIME (iTime*TAU)

const mat2 frot = mat2(0.80, 0.60, -0.60, 0.80);

void rot(inout vec2 p, float a) {
  float c = cos(a);
  float s = sin(a);
  p = vec2(c*p.x + s*p.y, -s*p.x + c*p.y);
}

float noise(vec2 p) {
  float a = sin(p.x);
  float b = sin(p.y);
  float c = 0.5 + 0.5*cos(p.x + p.y);
  float d = mix(a, b, c);
  return d;
}

float fbm(vec2 p) {    
  float f = 0.0;
  float a = 1.0;
  float s = 0.0;
  float m = 2.0;
  for (int x = 0; x < 4; ++x) {
    f += a*noise(p); p = frot*p*m;
    m += 0.01;
    s += a;
    a *= 0.5;
  }
  return f/s;
}

float warp(vec2 p, out vec2 v, out vec2 w) {

  vec2 vx = vec2(0.0, 0.0);
  vec2 vy = vec2(3.2, 1.3);

  vec2 wx = vec2(1.7, 9.2);
  vec2 wy = vec2(8.3, 2.8);

  rot(vx, TTIME/1000.0);
  rot(vy, TTIME/900.0);

  rot(wx, TTIME/800.0);
  rot(wy, TTIME/700.0);

  v = vec2(fbm(p + vx), fbm(p + vy));
  
  w = vec2(fbm(p + 3.0*v + wx), fbm(p + 3.0*v + wy));
  
  return fbm(p + 2.25*w);
}

vec3 normal(vec2 p) {
  vec2 v;
  vec2 w;
  vec2 e = vec2(0.0001, 0);
  
  vec3 n;
  n.x = warp(p + e.xy, v, w) - warp(p - e.xy, v, w);
  n.y = 2.0*e.x;
  n.z = warp(p + e.yx, v, w) - warp(p - e.yx, v, w);
  
  return normalize(n);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 q = fragCoord/iResolution.xy;
  vec2 p = -1. + 2. * q;
  p.x *= iResolution.x/iResolution.y;

  p += TTIME*0.01;
  p *= 2.0;
  vec3 col = vec3(1.0);
 
  vec2 v;
  vec2 w;
 
  float f = warp(p, v, w);
  vec3 n = normal(p);

  vec3 lig = normalize(vec3(0., 0.2, -0.4));
  rot(lig.xy, TTIME/10.0);
  float dif = max(dot(lig, n), 0.5);

  const vec3 col1 = vec3(0.1, 0.7, 0.8);
  const vec3 col2 = vec3(0.7, 0.3, 0.5);
  
  col = pow(dif, 0.75)*tanh(pow(abs(f + 0.5), 1.5)) + (length(v)*col1 + length(w)*col2);

  fragColor = vec4(col, 1.0);
}
'''


class StIcecreamWarpedFbm(GlslSource):
    name = 'st_icecream_warped_fbm'
    label = 'Icecream warped FBM'
    description = 'License: CC0 Result after playing around with warped FBMs for a few hours Based on: https://iquilezles.org/www/articles/warp/warp.htm'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
