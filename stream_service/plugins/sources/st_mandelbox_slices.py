"""Mandelbox slices: License CC0: Mandelbox slices More slices through 4D space

Source: https://www.shadertoy.com/view/WlcBD2
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Mandelbox slices
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Mandelbox slices
//  More slices through 4D space

#define TOLERANCE       0.0001
#define MAX_RAY_LENGTH  8.0
#define MAX_RAY_MARCHES 100
#define TIME            iTime
#define RESOLUTION      iResolution
#define LESS(a,b,c)     mix(a,b,step(0.,c))
#define SABS(x,k)       LESS((.5/(k))*(x)*(x)+(k)*.5,abs(x),abs(x)-(k))
#define ROT(a)          mat2(cos(a), sin(a), -sin(a), cos(a))
#define PI              3.141592654
#define TAU             (2.0*PI)

#define PERIOD          20.0
#define FADE            2.5
#define TIMEPERIOD      mod(TIME,PERIOD)
#define NPERIOD         floor(TIME/PERIOD)

const float fixed_radius2 = 1.8;
const float min_radius2   = 0.5;
const vec4  folding_limit = vec4(1.0);
const float scale         = -2.9-0.2;

float rand                = 0.5;

float hash(float co) {
  co += 6.0;
  return fract(sin(co*12.9898) * 13758.5453);
}

float tanh_approx(float x) {
//  return tanh(x);
  float x2 = x*x;
  return clamp(x*(27.0 + x2)/(27.0+9.0*x2), -1.0, 1.0);
}

float pmin(float a, float b, float k) {
  float h = clamp(0.5+0.5*(b-a)/k, 0.0, 1.0);
  
  return mix(b, a, h) - k*h*(1.0-h);
}

vec4 pmin(vec4 a, vec4 b, vec4 k) {
  vec4 h = clamp( 0.5+0.5*(b-a)/k, 0.0, 1.0);
  return mix(b, a, h) - k*h*(1.0-h);
}

void rot(inout vec2 v, float a) {
  float c = cos(a);
  float s = sin(a);
  v.xy = vec2(v.x*c + v.y*s, -v.x*s + v.y*c);
}

float box(vec4 p, vec4 b) {
  vec4 q = abs(p) - b;
  return length(max(q,0.0)) + min(max(max(q.x, q.w),max(q.y,q.z)),0.0);
}

void sphere_fold(inout vec4 z, inout float dz) {
  float r2 = dot(z, z);
    
  float t1 = (fixed_radius2 / min_radius2);
  float t2 = (fixed_radius2 / r2);

  if (r2 < min_radius2) {
    z  *= t1;
    dz *= t1;
  } else if (r2 < fixed_radius2) {
    z  *= t2;
    dz *= t2;
  }
}

void box_fold(float k, inout vec4 z, inout float dz) {
  // Soft clamp after suggestion from ollij
  vec4 zz = sign(z)*pmin(abs(z), folding_limit, vec4(k));
  z = zz * 2.0 - z;
}

float mb(vec4 z) {
  float off = iTime*0.25;
  vec4 offset = z;
  float dr = 1.0;
  float d = 1E6;
  float k = mix(0.05, 0.25, fract(37.0*rand));
  for(int n = 0; n < 4; ++n) {
    box_fold(k/dr, z, dr);
    sphere_fold(z, dr);
    z = scale * z + offset;
    dr = dr * abs(scale) + 1.0;
    float dd = min(d, (length(z) - 2.5)/abs(dr));
    if (n < 2) d = dd;
  }

  float d0 = (box(z, vec4(3.5, 3.5, 3.5, 3.5))-0.2) / abs(dr);
  return fract(17.0*rand) > 0.5 ? pmin(d0, d, 0.05) : d0;
}

float df(vec3 p) {
  const float s = 1.0/6.0;
  p -= vec3(0.0, 1.0, 0.0);
  p /= s;

  float a = fract(3.0*rand);
  const float aa = PI/4.0;
  const float bb = PI/4.0-aa*0.5;
  float b = bb+aa*fract(5.0*rand);
  float c = bb+aa*fract(7.0*rand);
  float d = bb+aa*fract(13.0*rand);
  vec4 pp = vec4(p.x, p.y, p.z, 2.0*a*a);

  rot(pp.xw, b);
  rot(pp.yw, c);
  rot(pp.zw, d);
  return mb(pp)*s;
}

float rayMarch(vec3 ro, vec3 rd, out int iter) {
  float t = 0.1;
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
  vec3  eps = vec3(.0005,0.0,0.0);
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

vec3 postProcess(vec3 col, vec2 q)  {
  col=pow(clamp(col,0.0,1.0),vec3(1.0/2.2)); 
  col=col*0.6+0.4*col*col*(3.0-2.0*col);  // contrast
  col=mix(col, vec3(dot(col, vec3(0.33))), -0.4);  // satuation
  col*=0.5+0.5*pow(19.0*q.x*q.y*(1.0-q.x)*(1.0-q.y),0.7);  // vigneting
  return col;
}

vec3 hsv2rgb(vec3 c) {
  const vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
  vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
  return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

vec3 render(vec3 ro, vec3 rd) {
  vec3 lightPos = 2.0*vec3(1.5, 3.0, 1.0);

  vec3 skyCol = vec3(0.0);

  int iter = 0;
  float t = rayMarch(ro, rd, iter);

  float ifade = 1.0-tanh_approx(3.0*float(iter)/float(MAX_RAY_MARCHES));

  vec3 pos = ro + t*rd;    
  vec3 nor = vec3(0.0, 1.0, 0.0);
  
  vec3 color = vec3(0.0);
  
  float h = hash(NPERIOD);
  
  if (t < MAX_RAY_LENGTH && pos.y > 0.0) {
    // Ray intersected object
    nor       = normal(pos);
    vec3 hsv  = (vec3(fract(h - 0.6 + 0.4+0.25*t), 1.0-ifade, 1.0));
    color     = hsv2rgb(hsv);
  } else if (pos.y > 0.0) {
    // Ray intersected sky
    return skyCol*ifade;
  } else {
    // Ray intersected plane
    t   = -ro.y/rd.y;
    pos = ro + t*rd;
    nor = vec3(0.0, 1.0, 0.0);
    vec2 pp = pos.xz*1.5;
    float m = 0.5+0.25*(sin(3.0*pp.x+TIME*2.1)+sin(3.3*pp.y+TIME*2.0));
    m *= m;
    m *= m;
    pp = fract(pp+0.5)-0.5;
    float dp = pmin(abs(pp.x), abs(pp.y), 0.025);
    vec3 hsv = vec3(0.4+mix(0.15,0.0, m), tanh_approx(mix(100.0, 10.0, m)*dp), 1.0);
    color = 5.5*hsv2rgb(hsv)*exp(-mix(30.0, 10.0, m)*dp);
  }

  vec3 lv   = lightPos - pos;
  float ll2 = dot(lv, lv);
  float ll  = sqrt(ll2);
  vec3 ld   = lv / ll;
  float sha = softShadow(pos, ld, ll, 0.01, 64.0);

  float dm  = min(1.0, 40.0/ll2);
  float dif = max(dot(nor,ld),0.0)*dm;
  float spe = pow(max(dot(reflect(-ld, nor), -rd), 0.), 10.);
  float l   = dif*sha;

  float lin = mix(0.2, 1.0, l);

  vec3 col = lin*color + spe*sha;

  float f = exp(-20.0*(max(t-3.0, 0.0) / MAX_RAY_LENGTH));
    
  return mix(skyCol, col , f)*ifade;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 q=fragCoord.xy/RESOLUTION.xy; 
  vec2 p = -1.0 + 2.0*q;
  p.x *= RESOLUTION.x/RESOLUTION.y;

  rand = hash(NPERIOD);

  vec3 ro = mix(0.3, 0.4, fract(23.0*rand))*vec3(2.0, 0, 0.2)+vec3(0.0, 1.25, 0.0);
  rot(ro.xz, sin(TIME*0.05));
  rot(ro.yz, sin(TIME*0.05*sqrt(0.5))*0.25);

  vec3 ww = normalize(vec3(0.0, 1.0, 0.0) - ro);
  vec3 uu = normalize(cross( vec3(0.0,1.0,0.0), ww));
  vec3 vv = normalize(cross(ww,uu));
  vec3 rd = normalize( p.x*uu + p.y*vv + (2.0+0.5*tanh_approx(length(p)))*ww);

  vec3 col = render(ro, rd);
  col = clamp(col, 0.0, 1.0);
  col *= smoothstep(0.0, FADE, TIMEPERIOD);
  col *= 1.0-smoothstep(PERIOD-FADE, PERIOD, TIMEPERIOD);
  fragColor = vec4(postProcess(col, q),1.0);
}
'''


class StMandelboxSlices(GlslSource):
    name = 'st_mandelbox_slices'
    label = 'Mandelbox slices'
    description = 'License CC0: Mandelbox slices More slices through 4D space'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
