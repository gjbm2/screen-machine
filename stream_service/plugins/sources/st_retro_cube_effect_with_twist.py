"""Retro cube effect with twist: License CC0: Retro cube effect with twist We made a retro demo some time ago and wanted a version of the classic Amiga effect: Music: Hyperbased by Firefox:...

Source: https://www.shadertoy.com/view/flfXDn
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Retro cube effect with twist
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Retro cube effect with twist
//  We made a retro demo some time ago and in one of the effects
//  We wanted to recreate the classic "Amiga Cube that intersects a translucent plane"
//  But having accesss to a few more Teraflops than the Amiga we wanted to add a bit of a twist to it
//  Music: Hyperbased by Firefox: https://soundcloud.com/firefox-amigamusician/hyperbased

#define PI              3.141592654
#define TAU             (2.0*PI)
#define TIME            (iTime+155.0)
#define RESOLUTION      iResolution
#define ROT(a)          mat2(cos(a), sin(a), -sin(a), cos(a))
#define PCOS(a)         (0.5+0.5*cos(a))
#define PSIN(a)         (0.5+0.5*sin(a))
#define L2(x)           dot(x, x)
#define SCA(a)          vec2(sin(a), cos(a))
#define MISS            1E6
#define BTIME(n)        (n*beat+start)

#define CUBE_TOLERANCE       0.0001
#define CUBE_MAX_RAY_LENGTH  8.0
#define CUBE_MAX_RAY_MARCHES 80
#define CUBE_NORM_OFF        0.0005

const float beat            = 0.48;
const float start           = 41.1;
const float bounce_freq     = 0.5/beat;

const mat2 rot0             = ROT(0.00);
const vec3 std_gamma        = vec3(2.2);

const float cube_begin      = BTIME(240.0); // ~156
const float cube_flash0     = BTIME(334.0);
const float cube_flash1     = BTIME(335.0);
const float cube_end        = BTIME(336.0);

// GLOBAL MUTABLES

vec4  cube_g_plane       = vec4(normalize(vec3(1.0, 0.0, 0.0)), 0.0);
float cube_g_pw          = 0.0;
mat2 cube_g_rotxy        = rot0;
mat2 cube_g_rotxz        = rot0;
mat2 cube_g_rotxw        = rot0;
mat2 cube_g_rotyw        = rot0;
mat2 cube_g_rotzw        = rot0;


// -----------------------------------------------------------------------------
// COMMON
// -----------------------------------------------------------------------------

float pmin(float a, float b, float k) {
  float h = clamp( 0.5+0.5*(b-a)/k, 0.0, 1.0 );
  return mix( b, a, h ) - k*h*(1.0-h);
}

float rayPlane(vec3 ro, vec3 rd, vec4 p) {
  return -(dot(ro,p.xyz)+p.w)/dot(rd,p.xyz);
}

float tanh_approx(float x) {
//  return tanh(x);
  float x2 = x*x;
  return clamp(x*(27.0 + x2)/(27.0+9.0*x2), -1.0, 1.0);
}

float plane(vec3 p, vec4 plane) {
  return dot(plane.xyz, p)+plane.w;
}

float box(vec2 p, vec2 b) {
  vec2 d = abs(p)-b;
  return length(max(d,0.0)) + min(max(d.x,d.y),0.0);
}

float box(vec4 p, vec4 b) {
  vec4 q = abs(p) - b;
  return length(max(q,0.0)) + min(max(max(q.x, q.w),max(q.y,q.z)),0.0);
}

vec2 mod2(inout vec2 p, vec2 size) {
  vec2 c = floor((p + size*0.5)/size);
  p = mod(p + size*0.5,size) - size*0.5;
  return c;
}

vec3 hsv2rgb(vec3 c) {
  const vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
  vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
  return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

// Classic post processing
vec3 postProcess(vec3 col, vec2 q) {
  col = clamp(col, 0.0, 1.0);
  col = pow(col, 1.0/std_gamma);
  col = col*0.6+0.4*col*col*(3.0-2.0*col);
  col = mix(col, vec3(dot(col, vec3(0.33))), -0.4);
  col *=0.5+0.5*pow(19.0*q.x*q.y*(1.0-q.x)*(1.0-q.y),0.7);
  return col;
}

// -----------------------------------------------------------------------------
// CUBE
// -----------------------------------------------------------------------------

vec2 cube_mengerSponge(vec4 p) {
  float db = box(p, vec4(1.0));
  if(db > .125) vec2(db, db);

  float d_ = db;
  float res = d_;

  float s = 1.0;
  for(int m = 0; m < 4; ++m) {
    float ss = 0.75;
    vec4 a = mod(p*s, 2.0)-1.0;
    s *= 3.0;
    vec4 r = abs(1.0 - 3.0*abs(a));

    float da = max(max(r.x,r.y),r.w);
    float db = max(max(r.y,r.z),r.w);
    float dc = max(max(r.z,r.x),r.w);
    float dd = max(max(r.z,r.x),r.y);
    float df = length(r)-2.16;

    float du = da;
    du = min(du, db);
    du = min(du, dc);
    du = pmin(du, dd, ss); // Soften the edges a bit
    du = max(du, -df);
    du -= 1.0;
    du /= s;

    res = max(res, du);
  }

  return vec2(db, res);
}

float cube_intersectTransformPlane(vec3 ro, vec3 rd) {
  return rayPlane(ro, rd, cube_g_plane);
}

float cube_dtransformPlane(vec3 p) {
  return plane(p, cube_g_plane);
}

float cube_df(vec3 p) {
  float dp = cube_dtransformPlane(p);
  const float s = 1.0/3.0;
  p /= s;
  p.xy *= cube_g_rotxy;
  p.xz *= cube_g_rotxz;
  vec4 pp = vec4(p, cube_g_pw);
  pp.xw *= cube_g_rotxw;
  pp.yw *= cube_g_rotyw;
  pp.zw *= cube_g_rotzw;

  // TODO: Optimize
  vec2 dms = cube_mengerSponge(pp);

  float d0 = dms.x*s;
  float d2 = d0;
  d0 = max(dp, d0);

  float d1 = dms.y*s;
  d1 = max(-dp, d1);
  return max(d2, pmin(d0, d1, 0.05));
}

float cube_rayMarch(vec3 ro, vec3 rd, out int iter) {
  float t = 0.0;
  int i = 0;
  for (i = 0; i < CUBE_MAX_RAY_MARCHES; i++) {
    float d = cube_df(ro + rd*t);
    if (d < CUBE_TOLERANCE || t > CUBE_MAX_RAY_LENGTH) break;
    t += d;
  }
  iter = i;
  return t;
}

vec3 cube_normal(vec3 pos) {
  vec2  eps = vec2(CUBE_NORM_OFF,0.0);
  vec3 nor;
  nor.x = cube_df(pos+eps.xyy) - cube_df(pos-eps.xyy);
  nor.y = cube_df(pos+eps.yxy) - cube_df(pos-eps.yxy);
  nor.z = cube_df(pos+eps.yyx) - cube_df(pos-eps.yyx);
  return normalize(nor);
}

float cube_softShadow(vec3 pos, vec3 ld, float ll, float mint, float k) {
  const float minShadow = 0.25;
  float res = 1.0;
  float t = mint;
  for (int i=0; i<24; i++) {
    float d = cube_df(pos + ld*t);
    res = min(res, k*d/t);
    if (ll <= t) break;
    if(res <= minShadow) break;
    t += max(mint*0.2, d);
  }
  return clamp(res,minShadow,1.0);
}

vec3 cube_transformPlane(vec3 ro, vec3 rd, vec4 plane, out float ttp) {
  vec3 tnor = plane.xyz;
  float t = rayPlane(ro, rd, plane);
  ttp = t;
  if (t < 0.0) return vec3(0.0);
  vec3 tp = ro + t*rd;
  float td = cube_df(tp);
  td -= 0.025;
  float otd = td;
  td = abs(td)- 0.005;
  const vec3 tup = vec3(0.0, 1.0, 0.0);
  vec3 txx = normalize(cross(tnor, tup));
  vec3 tyy = normalize(cross(tnor, txx));
  vec2 tp2 = vec2(dot(txx, tp), dot(tyy, tp));
  float a  = mix(0.0, PI/4.0 + 0.5*(TIME-BTIME(288.0)), smoothstep(BTIME(288.0), BTIME(292.0), TIME));
  tp2 *= ROT(a);
  float tpd = box(tp2, 0.6*vec2(4.0/3.0, 1.0));
  float taa = 0.001;
  mod2(tp2, vec2(0.125));
  float tpgd = min(abs(tp2.x), abs(tp2.y));
  tpgd = max(tpgd, -otd);
  tpgd = max(tpgd, tpd);
  float tgd = tpd;
  tgd -= 0.0125;
  tgd = abs(tgd)- 0.005;
  tgd = min(tgd, td);

  const vec3 greenGlow = vec3(1.25, 2.0, 1.25);
  const vec3 redGlow = vec3(2.0, 1.25, 1.5);
  vec3 tcol = vec3(0.0125);
  tcol += greenGlow*exp(-max(tpgd, 0.0)*900.0);
  tcol += greenGlow*(1.0-abs(dot(rd, tnor)))*0.2*PSIN(500.*tp2.y);
  tcol = mix(vec3(0.0), tcol, smoothstep(-taa, taa, -(tpd-0.025)));
  tcol += redGlow*exp(-max(tgd, 0.0)*100.0);
  return tcol;
}

vec3 cube_render(in vec3 ro, in vec3 rd) {
  vec3 lightPos = 2.0*vec3(1.5, 3.0, 1.0);

  float alpha   = 0.05*TIME;
  vec3 tnor     = normalize(vec3(1.0, 0.0, 0.0));
  tnor.xy       *= ROT(PI*(1.0-cos(sqrt(0.3)*max(TIME-BTIME(296.0), 0.0))));
  tnor.xz       *= ROT(PI*(1.0-cos(sqrt(0.15)*max(TIME-BTIME(304.0), 0.0))));

  float tm      = -0.5*(cos((2.0*TAU/(4.0/beat))*max(TIME-BTIME(292.0), 0.0)));
  tm  = mix(0.75 , tm, smoothstep(BTIME(272.0), BTIME(288.0), TIME));
  tm  = mix(-0.75, tm, smoothstep(BTIME(248.0), BTIME(272.0), TIME));
  tm  = mix(-3.0 , tm, smoothstep(BTIME(244.0), BTIME(248.0), TIME));

  vec4 plane    = vec4(tnor, tm);

  cube_g_plane       = plane;
  cube_g_rotxy       = ROT(TIME);
  cube_g_rotxz       = ROT(TIME*sqrt(0.5));
  cube_g_pw          = 0.5*cos(alpha*sqrt(2.0));
  cube_g_rotxw       = ROT(alpha);
  cube_g_rotyw       = ROT(alpha*sqrt(0.5));
  cube_g_rotzw       = ROT(alpha*sqrt(2.0));

//  tnor.xy *= g_rotxy;
  // background color
  vec3 skyCol = vec3(0.0);

  int iter = 0;
  float t = cube_rayMarch(ro, rd, iter);
  float tp;
  vec3 tcol = cube_transformPlane(ro, rd, plane, tp);
  tcol = mix(vec3(0.0), tcol, float(tp < t));

  float ifade = 1.0-tanh_approx(2.0*float(iter)/float(CUBE_MAX_RAY_MARCHES));

  vec3 pos = ro + t*rd;
  vec3 nor = vec3(0.0, 1.0, 0.0);

  vec3 color = vec3(0.0);

  float dp   = -(ro.y+1.)/rd.y;

  if (dp > 0.0 && dp < t) {
    // Ray intersected plane
    t   = dp;
    pos = ro + t*rd;
    nor = vec3(0.0, 1.0, 0.0);
    vec2 pp = pos.xz*1.5;
    float m = 0.5+0.25*(sin(3.0*pp.x+TIME*2.1)+sin(3.3*pp.y+TIME*2.0));
    m *= m;
    m *= m;
    pp = fract(pp+0.5)-0.5;
    float dp = pmin(abs(pp.x), abs(pp.y), 0.025);
    vec3 hsv = vec3(0.4+mix(0.15,0.0, m), tanh_approx(mix(100.0, 10.0, m)*dp), 1.0);
    color = 2.5*hsv2rgb(hsv)*exp(-mix(30.0, 10.0, m)*dp);
  } else if (t < CUBE_MAX_RAY_LENGTH) {
    // Ray intersected object
    nor        = cube_normal(pos);
    vec3 hsv   = (vec3(-0.2+0.25*t, 1.0-ifade, 1.0));
    color = hsv2rgb(hsv);
  } else {
    // Ray intersected sky
    return (skyCol)*ifade+tcol;
  }

  vec3 lv   = lightPos - pos;
  float ll2 = dot(lv, lv);
  float ll  = sqrt(ll2);
  vec3 ld   = lv / ll;
  float sha = cube_softShadow(pos, ld, ll, 0.01, 64.0);

  float dm  = min(1.0, 40.0/ll2);
  float dif = max(dot(nor,ld),0.0)*dm;
  float spe = pow(max(dot(reflect(-ld, nor), -rd), 0.), 10.);
  float l   = dif*sha;

  float lin = mix(0.2, 1.0, l);

  vec3 col = lin*color + spe*sha;

  float f = exp(-20.0*(max(t-3.0, 0.0) / CUBE_MAX_RAY_LENGTH));

  return (mix(skyCol, col , f))*ifade+tcol;
}

vec3 cube_effect(vec2 p, vec2 q) {
  float m = smoothstep(BTIME(264.0), BTIME(272.0), TIME);
  float tm = TIME-BTIME(264.0);
  // camera
  vec3 ro = mix(1.0, 0.6, m)*vec3(2.0, 0, 0.2)+vec3(0.0, 0.25, 0.0);
  ro.xz *= ROT(mix(0.0, tm*0.25, m));
  ro.yz *= ROT(-(1.0-PCOS(tm*0.25*sqrt(0.5)))*0.25);
  vec3 ww = normalize(vec3(0.0, 0.0, 0.0) - ro);
  vec3 uu = normalize(cross( vec3(0.0,1.0,0.0), ww ));
  vec3 vv = normalize(cross(ww,uu));
  vec3 rd = normalize( p.x*uu + p.y*vv + 2.5*ww );

  return cube_render(ro, rd);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 q = fragCoord/RESOLUTION.xy;
  vec2 p = -1. + 2. * q;
  p.x *= RESOLUTION.x/RESOLUTION.y;

  vec3 col = cube_effect(p, q);

  col = postProcess(col, q);
  col = mix(vec3(1.0), col, smoothstep(cube_begin+0.25, cube_begin+1.5, TIME));

  fragColor = vec4(col, 1.0);
}
'''


class StRetroCubeEffectWithTwist(GlslSource):
    name = 'st_retro_cube_effect_with_twist'
    label = 'Retro cube effect with twist'
    description = 'License CC0: Retro cube effect with twist We made a retro demo some time ago and wanted a version of the classic Amiga effect: Music: Hyperbased by Firefox:...'
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'music'}
    params = scene_params({}, render_scale=0.45)
