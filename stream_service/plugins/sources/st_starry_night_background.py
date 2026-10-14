"""Starry night background: License CC0: Starry night background Created for another shader but thought the background could be useful to others so extracted it

Source: https://www.shadertoy.com/view/fsjGDy
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Starry night background
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Starry night background
//  Created for another shader but thought the background could be useful to others so extracted it

// Controls how many layers of stars
#define LAYERS            5.0

#define PI                3.141592654
#define TAU               (2.0*PI)
#define TIME              iTime
#define RESOLUTION        iResolution
#define ROT(a)            mat2(cos(a), sin(a), -sin(a), cos(a))
#define PCOS(x)           (0.5 + 0.5*cos(x))
#define TTIME             (TAU*TIME)

// https://stackoverflow.com/questions/15095909/from-rgb-to-hsv-in-opengl-glsl
vec3 hsv2rgb(vec3 c) {
  const vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
  vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
  return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

// http://mercury.sexy/hg_sdf/
vec2 mod2(inout vec2 p, vec2 size) {
  vec2 c = floor((p + size*0.5)/size);
  p = mod(p + size*0.5,size) - size*0.5;
  return c;
}

vec2 hash2(vec2 p) {
  p = vec2(dot (p, vec2 (127.1, 311.7)), dot (p, vec2 (269.5, 183.3)));
  return fract(sin(p)*43758.5453123);
}

vec3 toSpherical(vec3 p) {
  float r   = length(p);
  float t   = acos(p.z/r);
  float ph  = atan(p.y, p.x);
  return vec3(r, t, ph);
}

vec3 postProcess(vec3 col, vec2 q)  {
  col=pow(clamp(col,0.0,1.0),vec3(1.0/2.2)); 
  col=col*0.6+0.4*col*col*(3.0-2.0*col);  // contrast
  col=mix(col, vec3(dot(col, vec3(0.33))), -0.4);  // satuation
  col*=0.5+0.5*pow(19.0*q.x*q.y*(1.0-q.x)*(1.0-q.y),0.7);  // vigneting
  return col;
}

vec3 stars(vec3 ro, vec3 rd) {
  vec3 col = vec3(0.0);
  vec3 srd = toSpherical(rd.xzy);
  
  const float m = LAYERS;

  for (float i = 0.0; i < m; ++i) {
    vec2 pp = srd.yz+0.5*i;
    float s = i/(m-1.0);
    vec2 dim  = vec2(mix(0.05, 0.003, s)*PI);
    vec2 np = mod2(pp, dim);
    vec2 h = hash2(np+127.0+i);
    vec2 o = -1.0+2.0*h;
    float y = sin(srd.y);
    pp += o*dim*0.5;
    pp.y *= y;
    float l = length(pp);
  
    float h1 = fract(h.x*109.0);
    float h2 = fract(h.x*113.0);
    float h3 = fract(h.x*127.0);

    vec3 hsv = vec3(fract(0.025-0.4*h1*h1), mix(0.5, 0.125, s), 1.0);
    vec3 scol = mix(8.0*h2, 0.25*h2*h2, s)*hsv2rgb(hsv);

    vec3 ccol = col+ exp(-(2000.0/mix(2.0, 0.25, s))*max(l-0.001, 0.0))*scol;
    col = h3 < y ? ccol : col;
  }
  
  return col;
}

vec3 grid(vec3 ro, vec3 rd) {
  vec3 srd = toSpherical(rd.xzy);
  
  const float m = 1.0;

  const vec2 dim = vec2(1.0/8.0*PI);
  vec2 pp = srd.yz;
  vec2 np = mod2(pp, dim);

  vec3 col = vec3(0.0);

  float y = sin(srd.y);
  float d = min(abs(pp.x), abs(pp.y*y));
  
  float aa = 2.0/RESOLUTION.y;
  
  col += 2.0*vec3(0.5, 0.5, 1.0)*exp(-2000.0*max(d-0.00025, 0.0));
  
  return 0.25*tanh(col);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 q = fragCoord.xy/RESOLUTION.xy; 
  vec2 p = -1.0 + 2.0*q;
  p.x *= RESOLUTION.x/RESOLUTION.y;

  vec3 ro = mix(0.5, 0.25, PCOS(TTIME/120.0*sqrt(3.0)))*vec3(2.0, 0, 0.2)+vec3(0.0, -0.125, 0.0);
  ro.yx *= ROT(TTIME/120.0*sqrt(0.5));
  ro.xz *= ROT((TAU*(TIME-14.0)/120.0));
  vec3 la = vec3(0.0, 0.0, 0.0);

  vec3 ww = normalize(la - ro);
  vec3 uu = normalize(cross( vec3(0.0,1.0,0.0), ww));
  vec3 vv = normalize(cross(ww,uu));
  const float rdd = 2.0;
  vec3 rd = normalize(p.x*uu + p.y*vv + rdd*ww);

  vec3 col = stars(ro, rd);
  col += grid(ro, rd);
  
  fragColor = vec4(postProcess(col, q),1.0);
}
'''


class StStarryNightBackground(GlslSource):
    name = 'st_starry_night_background'
    label = 'Starry night background'
    description = 'License CC0: Starry night background Created for another shader but thought the background could be useful to others so extracted it'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
