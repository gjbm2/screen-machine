"""Abstract galaxy: License CC0: Abstract Galaxy Been working on scifi shaders, while this is not "realistic" looking I find it nice to look at

Source: https://www.shadertoy.com/view/3sBBRw
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Abstract Galaxy
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Abstract Galaxy
//	Been working on scifi shaders, while this is not "realistic" looking I find it nice to look at
#define PI           3.141592654
#define TAU          (2.0*PI)

#define TIME         (iTime*0.2)
#define RESOLUTION   iResolution

#define LESS(a,b,c) mix(a,b,step(0.,c))

#define SABS(x,k)    LESS((.5/k)*x*x+k*.5,abs(x),abs(x)-k)


vec2 toPolar(vec2 p) {
  return vec2(length(p), atan(p.y, p.x));
}

vec2 toRect(vec2 p) {
  return p.x*vec2(cos(p.y), sin(p.y));
}

vec2 mod2(inout vec2 p, vec2 size) {
  vec2 c = floor((p + size*0.5)/size);
  p = mod(p + size*0.5,size) - size*0.5;
  return c;
}

float noise1(vec2 p) {
  float s = 1.0;

  p *= tanh(0.1*length(p));

  float a = cos(p.x);
  float b = cos(p.y);

  float c = cos(p.x*sqrt(3.5));
  float d = cos(p.y*sqrt(1.5));

  return a*b*c*d;
}

void rot(inout vec2 p, float a) {
  float c = cos(a);
  float s = sin(a);
  p = vec2(c*p.x + s*p.y, -s*p.x + c*p.y);
}

float galaxy(vec2 p, float a, float z) {
  vec2 pp = toPolar(p);
  pp.y += pp.x*3.0 + a;
  p = toRect(pp);
  
  p *= z;
  
  return noise1(p);
}

float height(vec2 p) {
  p *= 0.75;
  float s = 0.0;
  float a = 1.0;
  float f = 15.0;
  float d = 0.0;
  for (int i = 0; i < 3; ++i) {
    float g = a*galaxy(p, -0.5*TIME + TIME*(0.2*pow(float(i), 1.0)), f);
    s += g;
    a *= sqrt(0.5);
    f *= sqrt(2.0);
    d += a;
  }
  
  float l = length(p);
  
  return SABS((-0.25+ s/d), 0.5)*exp(-5.5*l*l);
}

vec3 normal(vec2 p) {
  vec2 eps = vec2(0.000125, 0.0);
  
  vec3 n;
  
  n.x = height(p - eps.xy) - height(p + eps.xy);
  n.y = 2.0*eps.x;
  n.z = height(p - eps.yx) - height(p + eps.yx);
  
  return normalize(n);
}

vec3 postProcess(vec3 col, vec2 q)  {
  col=pow(clamp(col,0.0,1.0),vec3(0.75)); 
  col=col*0.6+0.4*col*col*(3.0-2.0*col);  // contrast
  col=mix(col, vec3(dot(col, vec3(0.33))), -0.4);  // satuation
  col*=0.5+0.5*pow(19.0*q.x*q.y*(1.0-q.x)*(1.0-q.y),0.7);  // vigneting
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

  float l = length(p);
  float tl = tanh(l);

  float h = height(p);
  float th = tanh(h);
  vec3 n = normal(p);
  
  float diff1 = max(dot(ld1, n), 0.0);
  float diff2 = max(dot(ld2, n), 0.0);

  vec3 col = vec3(0.0);
  col += vec3(0.5, 0.5, 0.75)*h;
  col += 0.5*pow(diff1, 10.0);
  col += 0.25*pow(diff2, 2.0);
  col += pow(vec3(0.5)*h, n.y*1.75*(mix(vec3(0.5, 1.0, 1.5), vec3(0.5, 1.0, 1.5).zyx, 1.25*tl)));
  col += 0.9*vec3(1.0, 0.9, 0.75)*exp(-10.0*l*l);

  col = postProcess(col, q);

  fragColor = vec4(col, 1.0);
}

'''


class StAbstractGalaxy(GlslSource):
    name = 'st_abstract_galaxy'
    label = 'Abstract galaxy'
    description = 'License CC0: Abstract Galaxy Been working on scifi shaders, while this is not "realistic" looking I find it nice to look at'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
