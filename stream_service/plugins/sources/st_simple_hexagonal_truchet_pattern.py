"""Simple hexagonal truchet pattern: License CC0: Simple hexagonal truchet pattern Nothing fancy, wanted a simple shader that illustrated simple hexagonal truchet patterns

Source: https://www.shadertoy.com/view/WtSyDW
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Simple hexagonal truchet pattern
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Simple hexagonal truchet pattern
//  Nothing fancy, wanted a simple shader that illustrated simple hexagonal truchet patterns

#define PI  3.141592654
#define TAU (2.0*PI)

float hash(in vec2 co) {
  return fract(sin(dot(co.xy ,vec2(12.9898,58.233))) * 13758.5453);
}

float pcos(float a) {
  return 0.5 + 0.5*cos(a);
}

#define MROT(a) mat2(cos(a), sin(a), -sin(a), cos(a))

const mat2 rot120   = MROT(TAU/3.0);
const vec2 sz       = vec2(1.0, sqrt(3.0));
const vec2 hsz      = 0.5*sz;
const vec2 n1       = normalize(sz.yx);
const vec2 n2       = n1*vec2(-1.0, 1.0);
const float radius  = 0.5/sqrt(3.0);
const vec2 off1     = 2.0*radius*n1;
const vec2 off2     = rot120*off1;
const vec2 off3     = rot120*off2;

vec2 hextile(inout vec2 p) {
  // See Art of Code: Hexagonal Tiling Explained!
  // https://www.youtube.com/watch?v=VmrIDyYiJBA

  vec2 p1 = mod(p, sz)-hsz;
  vec2 p2 = mod(p - hsz*1.0, sz)-hsz;
  vec2 p3 = mix(p2, p1, vec2(length(p1) < length(p2)));
  vec2 n = p3 - p;
  p = p3;

  return round(n*2.0)*0.5;
}

void rot(inout vec2 p, float a) {
  float c = cos(a);
  float s = sin(a);
  p = vec2(c*p.x + s*p.y, -s*p.x + c*p.y);
}

float circle(vec2 p, float r) {
  return length(p) - r;
}

float cell0(vec2 p) {
  float d0 = abs(p.y);
  float d1 = abs(dot(n1, p));
  float d2 = abs(dot(n2, p));
  float d3 = circle(p, radius);
 
  float d = 1E6;
  d = min(d, d0);
  d = min(d, d1);
  d = min(d, d2);
  d = min(d, d3);
   
  return d;
}

float cell1(vec2 p) {
  p = abs(p);
  float d0 = circle(p-off1, radius); 
  return d0;
}

float cell2(vec2 p) {
  float d0 = circle(p-off1, radius);
  float d1 = circle(p-off2, radius);
  float d2 = circle(p-off3, radius);
   
  float d = 1E6;
  d = min(d, d0);
  d = min(d, d1);
  d = min(d, d2);
 
  return d;
}

float df(vec2 p, float scale, float width, inout vec2 nn) {
   p /= scale;   
   nn = hextile(p);
   nn = round(nn);
   float r1 = hash(nn);
   float r2 = fract(23.0*r1);
   rot(p, floor(r2*6.0)* TAU/6.0);
   float d = 1E6;
   if (r1 < 0.5) {
     d = cell2(p);
   } else if (r1 < 0.8) {
     d = cell1(p);
   } else {
     d = cell0(p);
   }
   d *= scale;
    
   float d1 = abs(d) - width;
   float d2 = abs(d) - width*2.0;
   d2 = abs(d2) - width*0.5;
   
   return min(d1, d2);

}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 q = fragCoord / iResolution.xy;
  vec2 p = -1.0 + 2.0*q;
  p.x *= iResolution.x/iResolution.y;
    
  float aa = 4.0 / iResolution.y;
  float tm = iTime*0.1;
    
  p += vec2(cos(tm), sin(tm*sqrt(0.5)));

  vec2 nn = vec2(0.0);
  float d = df(p, 0.25, 0.01, nn);

  vec3 col = vec3(0.0);
  
  col += vec3(0.5 + 0.5*sin(nn*10.0), 1.0)*smoothstep(0.0, aa, -d);
//  col += vec3(0.5, 0.3, 0.9)*pcos(500*d);
  
  fragColor = vec4(col, 1.0);
}


'''


class StSimpleHexagonalTruchetPattern(GlslSource):
    name = 'st_simple_hexagonal_truchet_pattern'
    label = 'Simple hexagonal truchet pattern'
    description = 'License CC0: Simple hexagonal truchet pattern Nothing fancy, wanted a simple shader that illustrated simple hexagonal truchet patterns'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
