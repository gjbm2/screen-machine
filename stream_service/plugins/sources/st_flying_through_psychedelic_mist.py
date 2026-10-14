"""Flying through psychedelic mist: License CC0: Flying through psychedelic mist Messing around with colors and FBM. Set BPM to some value that match your music.

Source: https://www.shadertoy.com/view/wl2yzt
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Flying through psychedelic mist
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Flying through psychedelic mist
// Messing around with colors and FBM. 

// Set BPM to some value that match your music.
#define BPM             30.0

#define GAMMAWEIRDNESS
#define QUINTIC

#define PI              3.141592654
#define TAU             (2.0*PI)
#define TIME            iTime
#define RESOLUTION      iResolution
#define MROT(a) mat2(cos(a), sin(a), -sin(a), cos(a))

const mat2 rotSome          = MROT(1.0);
const vec3 std_gamma        = vec3(2.2, 2.2, 2.2);

vec3 hsv2rgb(vec3 c) {
  const vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
  vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
  return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

float hash(in vec2 co) {
  return fract(sin(dot(co, vec2(12.9898,58.233))) * 13758.5453);
}

float psin(float a) {
  return 0.5 + 0.5*sin(a);
}

float vnoise(vec2 x) {
  vec2 i = floor(x);
  vec2 w = fract(x);
    
#ifdef QUINTIC
  // quintic interpolation
  vec2 u = w*w*w*(w*(w*6.0-15.0)+10.0);
#else
  // cubic interpolation
  vec2 u = w*w*(3.0-2.0*w);
#endif    

  float a = hash(i+vec2(0.0,0.0));
  float b = hash(i+vec2(1.0,0.0));
  float c = hash(i+vec2(0.0,1.0));
  float d = hash(i+vec2(1.0,1.0));
    
  float k0 =   a;
  float k1 =   b - a;
  float k2 =   c - a;
  float k3 =   d - c + a - b;

  float aa = mix(a, b, u.x);
  float bb = mix(c, d, u.x);
  float cc = mix(aa, bb, u.y);
  
  return k0 + k1*u.x + k2*u.y + k3*u.x*u.y;
}

vec3 alphaBlendGamma(vec3 back, vec4 front, vec3 gamma) {
  vec3 colb = max(back.xyz, 0.0);
  vec3 colf = max(front.xyz, 0.0);;
  
  colb = pow(colb, gamma);
  colf = pow(colf, gamma);
  vec3 xyz = mix(colb, colf.xyz, front.w);
  return pow(xyz, 1.0/gamma);
}

vec3 offset_0(float z) {
  float a = z;
  vec2 p = vec2(0.0);
  return vec3(p, z);
}

vec3 offset_1(float z) {
  float a = z/3.0;
  vec2 p = -2.0*0.075*(vec2(cos(a), sin(a*sqrt(2.0))) + vec2(cos(a*sqrt(0.75)), sin(a*sqrt(0.5))));
  return vec3(p, z);
}

vec3 offset(float z) {
  return offset_1(z);
}

vec3 doffset(float z) {
  float eps = 0.1;
  return 0.5*(offset(z + eps) - offset(z - eps))/eps;
}

vec3 ddoffset(float z) {
  float eps = 0.1;
  return 0.125*(doffset(z + eps) - doffset(z - eps))/eps;
}

vec3 skyColor(vec3 ro, vec3 rd) {
  return mix(1.5*vec3(0.75, 0.75, 1.0), vec3(0.0), length(2.0*rd.xy));
}

float height(vec2 p, float n, out vec2 diff) {
  const float aan = 0.45;
  const float ppn = 2.0+0.2;
  
  float s = 0.0;
  float d = 0.0;
  float an = 1.0;
  vec2 pn = 4.0*p+n*10.0;
  vec2 opn = pn;

  const int md = 1;
  const int mx = 4;
  
  for (int i = 0; i < md; ++i) {
    s += an*(vnoise(pn)); 
    d += abs(an);
    pn *= ppn*rotSome;
    an *= aan; 
  }

  for (int i = md; i < mx; ++i) {
    s += an*(vnoise(pn)); 
    d += abs(an);
    pn *= ppn*rotSome;
    an *= aan; 
    pn += (3.0*float(i+1))*s-TIME*5.5;     // Fake warp FBM
  }

  s /= d;

  diff = (pn - opn);

  return s;
}

vec4 plane(vec3 ro, vec3 rd, vec3 pp, float aa, float n) {
  vec2 p = pp.xy;
  float z = pp.z;
  float nz = pp.z-ro.z;
  
  vec2 diff;
  vec2 hp = p;
  hp -= nz*0.125*0.5*vec2(1.0, -0.5);
  hp -= n;
  float h = height(hp, n, diff);
  float gh = (vnoise(0.25*(p+vec2(n))));
  h *= mix(0.75, 1.0, gh);
  h = abs(h);
  
  vec3 col = vec3(0.0);
  col = vec3(h);
  float huen = (length(diff)/200.0);
  float satn = 1.0;
  float brin = h;
  col = hsv2rgb(vec3(huen, satn, brin));
  
  float t = sqrt(h)*(smoothstep(0.0, 0.5, length(pp - ro)))*smoothstep(0.0, mix(0.4, 0.75, pow(psin(TIME*TAU*BPM/60.0), 4.0)), length(p));
  return vec4(col, t);
}
vec3 color(vec3 ww, vec3 uu, vec3 vv, vec3 ro, vec2 p) {
  float lp = length(p);
//  vec3 rd = normalize(p.x*uu + p.y*vv + (3.00-1.0*tanh(lp))*ww);
  vec3 rd = normalize(p.x*uu + p.y*vv + (2.00+tanh(lp))*ww);

  float planeDist = 1.0-0.25;
  const int furthest = 6;
  const int fadeFrom = furthest-4;

  float nz = floor(ro.z / planeDist);

  vec3 skyCol = skyColor(ro, rd);  
  
  vec3 col = skyCol;

  for (int i = furthest; i >= 1 ; --i) {
    float pz = planeDist*nz + planeDist*float(i);
    
    float pd = (pz - ro.z)/rd.z;
    
    if (pd > 0.0) {
      vec3 pp = ro + rd*pd;
   
      float aa = length(dFdy(pp));

      vec4 pcol = plane(ro, rd, pp, aa, nz+float(i));
      float nz = pp.z-ro.z;
      float fadeIn = (1.0-smoothstep(planeDist*float(fadeFrom), planeDist*float(furthest), nz));
      float fadeOut = smoothstep(0.0, planeDist*0.1, nz);
      pcol.xyz = mix(skyCol, pcol.xyz, (fadeIn));
      pcol.w *= fadeOut;

      vec3 gamma = std_gamma;
#ifdef GAMMAWEIRDNESS
      float ga = pp.z;
      vec3 gg = vec3(psin(ga), psin(ga*sqrt(0.5)), psin(ga*2.0));
      gamma *= mix(vec3(0.1), vec3(10.0), gg);
#endif
      col = alphaBlendGamma(col, pcol, gamma);
    } else {
      break;
    }
    
  }
  
  return col;
}

vec3 postProcess(vec3 col, vec2 q)  {
  col=pow(clamp(col,0.0,1.0),vec3(0.75)); 
  col=col*0.6+0.4*col*col*(3.0-2.0*col);
  col=mix(col, vec3(dot(col, vec3(0.33))), -0.4);
  col*=0.5+0.5*pow(19.0*q.x*q.y*(1.0-q.x)*(1.0-q.y),0.7);
  return col;
}

vec3 effect(vec2 p, vec2 q) {
  float tm = TIME;
  vec3 ro   = offset(tm);
  vec3 dro  = doffset(tm);
  vec3 ddro = ddoffset(tm);

  vec3 ww = normalize(dro);
  vec3 uu = normalize(cross(normalize(vec3(0.0,1.0,0.0)+ddro), ww));
  vec3 vv = normalize(cross(ww, uu));
  
  vec3 col = color(ww, uu, vv, ro, p);
  col = postProcess(col, q);
  return col;
}

void mainImage(out vec4 fragColor, vec2 fragCoord) {
  vec2 q = fragCoord/RESOLUTION.xy;
  vec2 p = -1. + 2. * q;
  p.x *= RESOLUTION.x/RESOLUTION.y;
  
  vec3 col = effect(p, q);
  
  fragColor = vec4(col, 1.0);
}

'''


class StFlyingThroughPsychedelicMist(GlslSource):
    name = 'st_flying_through_psychedelic_mist'
    label = 'Flying through psychedelic mist'
    description = 'License CC0: Flying through psychedelic mist Messing around with colors and FBM. Set BPM to some value that match your music.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
