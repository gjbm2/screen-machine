"""Random little fractal: License CC0: Random little fractal Weird but to me appealing results after a bit of tinkering with no clear goals

Source: https://www.shadertoy.com/view/tl3fzH
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Random little fractal
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Random little fractal
// Weird but to me appealing results after a bit of tinkering with no clear goals

#define TIME            iTime
#define RESOLUTION      iResolution
#define PI              3.141592654
#define TAU             (2.0*PI)
#define L2(x)           dot(x, x)
#define ROT(a)          mat2(cos(a), sin(a), -sin(a), cos(a))
#define PSIN(x)         (0.5+0.5*sin(x))
#define LESS(a,b,c)     mix(a,b,step(0.,c))
#define SABS(x,k)       LESS((.5/(k))*(x)*(x)+(k)*.5,abs(x),abs(x)-(k))

const float fixed_radius2 = 1.9;
const float min_radius2   = 0.5;
const float folding_limit = 1.0;
const float scale         = -2.8;

void sphere_fold(inout vec3 z, inout float dz) {
    float r2 = dot(z, z);
    if(r2 < min_radius2) {
        float temp = (fixed_radius2 / min_radius2);
        z *= temp;
        dz *= temp;
    } else if(r2 < fixed_radius2) {
        float temp = (fixed_radius2 / r2);
        z *= temp;
        dz *= temp;
    }
}

vec3 polySoftMin3(vec3 a, vec3 b, vec3 k) {
  vec3 h = clamp( 0.5+0.5*(b-a)/k, 0.0, 1.0);
  
  return mix(b, a, h) - k*h*(1.0-h);
}

void box_fold(inout vec3 z, inout float dz) {
  const float k = 0.05;
  // Soft clamp after suggestion from ollij
  vec3 zz = sign(z)*polySoftMin3(abs(z), vec3(folding_limit), vec3(k));
  // Hard clamp
  // z = clamp(z, -folding_limit, folding_limit);
  z = zz * 2.0 - z;
}

float sphere(vec3 p, float t) {
  return length(p)-t;
}

float torus(vec3 p, vec2 t) {
  vec2 q = vec2(length(p.xz)-t.x,p.y);
  return length(q)-t.y;
}

float mb(vec3 z) {
    vec3 offset = z;
    float dr = 1.0;
    float fd = 0.0;
    for(int n = 0; n < 5; ++n) {
        box_fold(z, dr);
        sphere_fold(z, dr);
        z = scale * z + offset;
        dr = dr * abs(scale) + 1.0;        
        float r1 = sphere(z, 5.0);
        float r2 = torus(z, vec2(8.0, 1));
        r2 = abs(r2) - 0.25;
        float r = n < 4 ? r2 : r1;        
        float dd = r / abs(dr);
        if (n < 3 || dd < fd) {
          fd = dd;
        }
    }
    return fd;
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

float weird(vec2 p) {
  const float s = 0.55;
  p /= s;
  float rep = 20.0;
  float ss = 0.05*6.0/rep;
  vec3 p3 = vec3(p.x, p.y, PSIN(TIME*0.53));
  p3.yz *= ROT(TIME*0.1);
  float n = smoothKaleidoscope(p3.xy, ss, rep);
  return mb(p3)*s;
}
float df(vec2 p) {
  float d = weird(p);
  return d;
}

vec3 color(vec2 p) {
  float aa   = 2.0/RESOLUTION.y;
  const float lw = 0.0235;
  const float lh = 1.25;

  const vec3 lp1 = vec3(0.5, lh, 0.5);
  const vec3 lp2 = vec3(-0.5, lh, 0.5);

  float d = df(p);

  float b = -0.125;
  float t = 10.0;

  vec3 ro = vec3(0.0, t, 0.0);
  vec3 pp = vec3(p.x, 0.0, p.y);

  vec3 rd = normalize(pp - ro);

  vec3 ld1 = normalize(lp1 - pp);
  vec3 ld2 = normalize(lp2 - pp);

  float bt = -(t-b)/rd.y;
  
  vec3 bp   = ro + bt*rd;
  vec3 srd1 = normalize(lp1-bp);
  vec3 srd2 = normalize(lp2-bp);
  float bl21= L2(lp1-bp);
  float bl22= L2(lp2-bp);

  float st1= (0.0-b)/srd1.y;
  float st2= (0.0-b)/srd2.y;
  vec3 sp1 = bp + srd1*st1;
  vec3 sp2 = bp + srd2*st1;

  float sd1= df(sp1.xz);
  float sd2= df(sp2.xz);

  vec3 col  = vec3(0.0);
  const float ss =15.0;
  
  col       += vec3(1.0)*(1.0-exp(-ss*(max((sd1+0.0*lw), 0.0))))/bl21;
  col       += vec3(0.5)*(1.0-exp(-ss*(max((sd2+0.0*lw), 0.0))))/bl22;
  col       = mix(col, vec3(1.0), smoothstep(-aa, aa, -d));  
  return col;
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
  
  vec3 col = color(p);
  
  col = postProcess(col, q);
  
  fragColor = vec4(col, 1.0);
}
'''


class StRandomLittleFractal(GlslSource):
    name = 'st_random_little_fractal'
    label = 'Random little fractal'
    description = 'License CC0: Random little fractal Weird but to me appealing results after a bit of tinkering with no clear goals'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
