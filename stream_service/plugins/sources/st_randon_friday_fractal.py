"""Randon friday fractal: License CC0: Random friday fractal Result after a bit of random coding on friday afternoon

Source: https://www.shadertoy.com/view/7tj3DG
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Random friday fractal
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Random friday fractal
// Result after a bit of random coding on friday afternoon

#define PI              3.141592654
#define TAU             (2.0*PI)
#define TIME            iTime
#define RESOLUTION      iResolution
#define ROT(a)          mat2(cos(a), sin(a), -sin(a), cos(a))

#define TOLERANCE       0.00001
#define MAX_RAY_LENGTH  10.0
#define MAX_RAY_MARCHES 50
#define NORM_OFF        0.0001

const vec3 std_gamma        = vec3(2.2);

float tanh_approx(float x) {
//  return tanh(x);
  float x2 = x*x;
  return clamp(x*(27.0 + x2)/(27.0+9.0*x2), -1.0, 1.0);
}

// From: https://stackoverflow.com/a/17897228/418488
vec3 hsv2rgb(vec3 c) {
  const vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
  vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
  return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

vec3 postProcess(vec3 col, vec2 q) {
  col = clamp(col, 0.0, 1.0);
  col = pow(col, 1.0/std_gamma);
  col = col*0.6+0.4*col*col*(3.0-2.0*col);
  col = mix(col, vec3(dot(col, vec3(0.33))), -0.4);
  col *=0.5+0.5*pow(19.0*q.x*q.y*(1.0-q.x)*(1.0-q.y),0.7);
  return col;
}

float box(vec3 p, vec3 b) {
  vec3 q = abs(p) - b;
  return length(max(q,0.0)) + min(max(q.x,max(q.y,q.z)),0.0);
}

float pmin(float a, float b, float k) {
  float h = clamp( 0.5+0.5*(b-a)/k, 0.0, 1.0 );
  return mix( b, a, h ) - k*h*(1.0-h);
}

float pmax(float a, float b, float k) {
  return -pmin(-a, -b, k);
}

vec3 pmin(vec3 a, vec3 b, float k) {
  vec3 h = clamp( 0.5+0.5*(b-a)/k, 0.0, 1.0 );
  return mix( b, a, h ) - k*h*(1.0-h);
}

vec3 pabs(vec3 a, float k) {
  return -pmin(a, -a, k);
}


float df(vec3 p) {
  float d = 1E6;

  const float zf = 2.;
  const vec3 nz = normalize(vec3(1.0, .0, -1.0));
  const vec3 ny = normalize(vec3(1.0, -1., 0.0));
  float z = 1.0;
  const float rsm = 0.125*0.25;
  const float a = 128.25; 
  const mat2 rxy = ROT(a);
  const mat2 ryz = ROT(a*sqrt(0.5));
  
  for (int i = 0; i < 7; ++i) {
    vec3 pp0 = p;
    vec3 pp1 = p;
    float dd0 = box(pp0, vec3(0.25))-0.01;
    float dd1 = length(pp1)-0.3;
    float dd  = dd0;
    dd =  pmax(dd, -dd1, 0.05);
    dd = min(dd, length(pp0)- 0.25);
    dd  /= z;
    
    z *= zf;
    p *= zf;
    p.xy *= rxy;
    p.yz *= ryz;
    p  = pabs(p, rsm);
    p -= nz*pmin(0.0, dot(p, nz), rsm)*2.0;
    p -= ny*pmin(0.0, dot(p, ny), rsm)*2.0;

    p -= vec3(0.85/zf, 0.0, 0.0);
    d = pmax(d, -(dd-0.1/z), 0.05/z);
    
    if(i > 2)
    d = min(d, dd);
  
  }
  return d;
}

float rayMarch(vec3 ro, vec3 rd, out int iter) {
  float t = 0.0;
  int i = 0;
  for (i = 0; i < MAX_RAY_MARCHES; i++) {
    float d = df(ro + rd*t);
    if (d < TOLERANCE || t > MAX_RAY_LENGTH) break;
    t += d;
  }
  iter = i;
  return t;
}

vec3 normal(vec3 pos) {
  vec2  eps = vec2(NORM_OFF,0.0);
  vec3 nor;
  nor.x = df(pos+eps.xyy) - df(pos-eps.xyy);
  nor.y = df(pos+eps.yxy) - df(pos-eps.yxy);
  nor.z = df(pos+eps.yyx) - df(pos-eps.yyx);
  return normalize(nor);
}

float softShadow(vec3 pos, vec3 ld, float ll, float mint, float k) {
  const float minShadow = 0.25;
  float res = 1.0;
  float t = mint;
  for (int i=0; i<24; i++) {
    float d = df(pos + ld*t);
    res = min(res, k*d/t);
    if (ll <= t) break;
    if(res <= minShadow) break;
    t += max(mint*0.2, d);
  }
  return clamp(res,minShadow,1.0);
}

vec3 render(vec3 ro, vec3 rd) {
  vec3 lightPos = vec3(1.0);
  float alpha   = 0.05*TIME;
  
  const vec3 skyCol = vec3(0.0);

  int iter    = 0;
  float t     = rayMarch(ro, rd, iter);
  if (t >= MAX_RAY_LENGTH) {
    return vec3(0.0);
  }

  vec3 pos    = ro + t*rd;
  vec3 nor    = normal(pos);
  vec3 refl   = reflect(rd, nor);
  float ii    = float(iter)/float(MAX_RAY_MARCHES);

  float ifade= 1.0-tanh_approx(1.25*ii);
  vec3 hsv   = vec3(1.25-t*0.5, mix(0.25, 1.0, ii), 1.0);
  vec3 color = hsv2rgb(hsv);

  vec3 lv   = lightPos - pos;
  float ll2 = dot(lv, lv);
  float ll  = sqrt(ll2);
  vec3 ld   = lv / ll;
  float sha = softShadow(pos, ld, ll*0.95, 0.01, 10.0);

  float dm  = 5.0/ll2;
  float dif = max(dot(nor,ld),0.0)*(dm+0.05);  
  float spe = pow(max(dot(refl, ld), 0.), 20.);
  float ao  = smoothstep(0.5, 0.1 , ii);
  float l   = mix(0.2, 1.0, dif*sha*ao);

  vec3 col = l*color + 2.0*spe*ao*sha;

  return col*ifade;
//  return vec3(ao);
}

vec3 effect3d(vec2 p, vec2 q) {
  float z   = TIME;
  vec3 cam  = vec3(1.0, 0.5, 0.0);
  float rt  = TAU*TIME/20.0;;
  cam.xy   *= ROT(sin(rt*sqrt(0.5))*0.5+0.0);
  cam.xz   *= ROT(sin(rt)*1.0-0.75);
  vec3 la   = vec3(0.0);
  vec3 dcam = normalize(la - cam);
  vec3 ddcam= vec3(0.0);
  
  vec3 ro = cam;
  vec3 ww = normalize(dcam);
  vec3 uu = normalize(cross(vec3(0.0,1.0,0.0)+ddcam*2.0, ww ));
  vec3 vv = normalize(cross(ww,uu));
  vec3 rd = normalize( p.x*uu + p.y*vv + 2.5*ww );

  return render(ro, rd);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 q = fragCoord/RESOLUTION.xy;
  vec2 p = -1. + 2. * q;
  p.x *= RESOLUTION.x/RESOLUTION.y;

  vec3 col = effect3d(p, q);

  col = postProcess(col, q);

  fragColor = vec4(col, 1.0);
}

'''


class StRandonFridayFractal(GlslSource):
    name = 'st_randon_friday_fractal'
    label = 'Randon friday fractal'
    description = 'License CC0: Random friday fractal Result after a bit of random coding on friday afternoon'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
