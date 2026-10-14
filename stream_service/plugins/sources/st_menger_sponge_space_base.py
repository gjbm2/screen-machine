"""Menger sponge space base: License CC0: Menger sponge space base Tweaking menger sponges revealed a space based hidden inside

Source: https://www.shadertoy.com/view/WtcfDB
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Menger sponge space base
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Menger sponge space base
//  Tweaking menger sponges revealed a space based hidden inside

#define TIME            iTime
#define TOLERANCE       0.0001
#define MAX_RAY_LENGTH  9.0
#define HIT_SKY         1E6
#define MAX_RAY_MARCHES 100
#define LESS(a,b,c)     mix(a,b,step(0.,c))
#define SABS(x,k)       LESS((.5/(k))*(x)*(x)+(k)*.5,abs(x),abs(x)-(k))
#define ROT(a)          mat2(cos(a), sin(a), -sin(a), cos(a))
#define PI              3.141592654
#define TAU             (2.0*PI)
#define PSIN(x)         (0.5+0.5*sin(x))
#define PERIOD          20.0
#define PERIODTIME      mod(TIME,PERIOD)
#define PERIODN         int(mod(floor(TIME/PERIOD), 3.0))
#define FADE            1.0

const vec3  sunDirection        = normalize(vec3(-2.5, 3.5, -10.0));
const vec3  sunColor1           = vec3(1.0, 0.8, 0.8);
const vec3  sunColor2           = vec3(1.0, 0.8, 0.9);
const vec3  smallSunDirection   = normalize(vec3(0.5, 0, -10.0));
const vec3  smallSunColor1      = vec3(1.0, 0.6, 0.6);
const vec3  smallSunColor2      = vec3(1.0, 0.3, 0.6);
const vec3  ringColor           = sqrt(vec3(0.95, 0.65, 0.45));
const vec4  planet              = vec4(150.0, 0.0, 180.0, 50.0)*1000.0;
const vec3  planetCol           = sqrt(vec3(0.3, 0.5, 0.9))*1.5;
const vec3  ringsNormal         = normalize(vec3(1.0, 02.25, 0.0));
const vec4  rings               = vec4(ringsNormal, -dot(ringsNormal, planet.xyz));
const mat2  rot45               = ROT(PI/4.0);
const mat2  rot1                = ROT(1.0);
const mat2  rot2                = ROT(2.0);
const vec3  center              = vec3(0.0, 0.0, 0.0);
const vec4  glowSphere          = vec4(center, 1.2);
const vec3  glowCol             = vec3(3.0, 2.0, 1.);
const vec3  baseCol             = vec3(0.45, 0.45, 0.5)*0.5;

float sphered(vec3 ro, vec3 rd, vec4 sph, float dbuffer) {
    float ndbuffer = dbuffer/sph.w;
    vec3  rc = (ro - sph.xyz)/sph.w;
  
    float b = dot(rd,rc);
    float c = dot(rc,rc) - 1.0;
    float h = b*b - c;
    if( h<0.0 ) return 0.0;
    h = sqrt( h );
    float t1 = -b - h;
    float t2 = -b + h;

    if( t2<0.0 || t1>ndbuffer ) return 0.0;
    t1 = max( t1, 0.0 );
    t2 = min( t2, ndbuffer );

    float i1 = -(c*t1 + b*t1*t1 + t1*t1*t1/3.0);
    float i2 = -(c*t2 + b*t2*t2 + t2*t2*t2/3.0);
    return (i2-i1)*(3.0/4.0);
}

float tanh_approx(float x) {
//  return tanh(x);
  float x2 = x*x;
  return clamp(x*(27.0 + x2)/(27.0+9.0*x2), -1.0, 1.0);
}

float sphere(vec3 p, float r) {
  return length(p) - r;
}

float box(vec3 p, vec3 b) {
  vec3 q = abs(p) - b;
  return length(max(q,0.0)) + min(max(q.x,max(q.y,q.z)),0.0);
}

float box(vec4 p, vec4 b) {
  vec4 q = abs(p) - b;
  return length(max(q,0.0)) + min(max(max(q.x, q.w),max(q.y,q.z)),0.0);
}

float rayPlane(vec3 ro, vec3 rd, vec4 p) {
  return -(dot(ro,p.xyz)+p.w)/dot(rd,p.xyz);
}

vec2 raySphere(vec3 ro, vec3 rd, vec3 ce, float ra) {
  vec3 oc = ro - ce;
  float b = dot(oc, rd);
  float c = dot(oc, oc) - ra*ra;
  float h = b*b - c;
  if (h<0.0) return vec2(-1.0); // no intersection
  h = sqrt(h);
  return vec2( -b-h, -b+h );
}

vec3 gasGiant(vec3 ro, vec3 rd, vec3 sunDir) {
  vec2 si = raySphere(ro, rd, planet.xyz, planet.w);
  float pi = rayPlane(ro, rd, rings);
  
  vec3 planetSurface = ro + si.x*rd;
  vec3 planetNormal = normalize(planetSurface - planet.xyz);
  float planetDiff = max(dot(planetNormal, sunDir), 0.0);
  float planetBorder = max(dot(planetNormal, -rd), 0.0);
  float planetLat = (planetSurface.x+planetSurface.y)*0.0005;
  vec3 planetCol = mix(1.3*planetCol, 0.3*planetCol, pow(PSIN(planetLat+1.0)*PSIN(sqrt(2.0)*planetLat+2.0)*PSIN(sqrt(3.5)*planetLat+3.0), 0.5));

  vec3 ringsSurface = ro + pi*rd;

  float borderTransparency = smoothstep(0.0, 0.1, planetBorder);
  
  float ringsDist = length(ringsSurface - planet.xyz)*1.0;
  float ringsPeriod = ringsDist*0.001;
  const float ringsMax = 150000.0*0.655;
  const float ringsMin = 100000.0*0.666;
  float ringsMul = pow(PSIN(ringsPeriod+1.0)*PSIN(sqrt(0.5)*ringsPeriod+2.0)*PSIN(sqrt(0.45)*ringsPeriod+4.0)*PSIN(sqrt(0.35)*ringsPeriod+5.0), 0.25);
  float ringsMix = PSIN(ringsPeriod*10.0)*PSIN(ringsPeriod*10.0*sqrt(2.0))*(1.0 - smoothstep(50000.0, 200000.0, pi));

  vec3 ringsCol = mix(vec3(0.125), 0.75*ringColor, ringsMix)*step(-pi, 0.0)*step(ringsDist, ringsMax)*step(-ringsDist, -ringsMin)*ringsMul;
  
  vec3 final = vec3(0.0);
    
  final += ringsCol*(step(pi, si.x) + step(si.x, 0.0));
  
  final += step(0.0, si.x)*pow(planetDiff, 0.75)*mix(planetCol, ringsCol, 0.0)*borderTransparency + ringsCol*(1.0 - borderTransparency);

  return final;
}

vec3 sunColor(vec3 ro, vec3 rd) {
  float diff = max(dot(rd, sunDirection), 0.0);
  float smallDiff = max(dot(rd, smallSunDirection), 0.0);
  vec3 col = vec3(0.0);

  col += pow(diff, 800.0)*sunColor1*8.0;
  col += pow(diff, 150.0)*sunColor2;

  col += pow(smallDiff, 8000.0)*smallSunColor1*1.0;
  col += pow(smallDiff, 400.0)*smallSunColor2*0.5;
  col += pow(smallDiff, 150.0)*smallSunColor2*0.5;

  return col;
}

vec3 skyColor(vec3 ro, vec3 rd) {
  vec3 scol = sunColor(ro, rd);
  vec3 gcol = gasGiant(ro+vec3(0.0, 0.0, 200000.0), rd, sunDirection);
  
  return scol+gcol;
}

float minda = 1E6;

float mengerSponge(vec4 p) {
  float db = box(p, vec4(1.175, 1.-0.1, 1.05, 1.0));
  if(db > .125) return db;
    
  float res = db;

  float s = 1.0;
  const int mc = 4;
  for(int m = 0; m < mc; ++m) {

    vec4 a = mod(p*s, 2.0)-1.0;
    s *= 3.0;
    vec4 r = abs(1.0 - 3.0*abs(a));

    r -= vec4(2.0, 2.0, 2.0, 2.0)+vec4(0.1, 0.1, 0.1, 0)*-2.;
    float da = sphere(r.xyz, 1.0); // w
    float db = box(r.yzw, vec3(1)); // x
    float dc = box(r.xzw, vec3(1)); // y
    float dd = box(r.xyw, vec3(1.2)); // z

    minda = min(minda, max(da, da));

    float du = da;
    du = min(du, db);
    du = min(du, dc);
    du = min(du, dd);
    float lw = mix(0.215, 0.05, float(m)/float(mc-1));
    du = abs(du)-lw;
    du /= s;

    res = max(res, -du);
  }
  
  return (res);
}

float df(vec3 p) {
  p -= center;
  vec4 pp = vec4(p, 0.);
  pp.xz *= rot1;
  pp.yz *= rot2;
  pp.zw *= rot45;
  pp.yw *= rot45;
  pp.xw *= rot45;
  float dm = mengerSponge(pp);
  return dm;
}

float rayMarch(vec3 ro, vec3 rd, out int iter) {
  float it = df(ro);
  float t = it;
  int i = 0;
  float mrl = MAX_RAY_LENGTH + it;
  for (i = 0; i < MAX_RAY_MARCHES; i++) {
    float d = df(ro + rd*t);
    if (d < TOLERANCE || t > mrl) break;
    t += d;
  }
  iter = i;
  return t > mrl ? HIT_SKY : t;
}

vec3 normal(vec3 pos, float e) {
  vec2  eps = vec2(e,0.0);
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
    float distance = df(pos + ld*t);
    res = min(res, k*distance/t);
    if (ll <= t) break;
    if(res <= minShadow) break;
    t += max(mint*0.2, distance);
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

vec3 render(vec3 ro, vec3 rd) {
  vec3 skyCol = skyColor(ro, rd);

  int iter = 0;
  float id = df(ro);
  float t = rayMarch(ro, rd, iter);

  float ifade = 1.0-tanh_approx(3.0*float(iter)/float(MAX_RAY_MARCHES));

  vec3 pos = ro + t*rd;    

  minda = 1E6;
  vec3 nor = normal(pos,0.00001+t*0.00075);
  float mind = 1.0;

  vec3 color = vec3(0.0);
  
  float sd = sphered(ro, rd, glowSphere, t);

  if (t < HIT_SKY) {
    // Ray intersected object
    color = baseCol*tanh_approx(2.5*max(minda*minda, 0.0));
  } else {
    // Ray intersected sky
    return mix(skyCol*sqrt(ifade), glowCol, sd);
  }

  vec3 ld1   = sunDirection;
  vec3 ld2   = smallSunDirection;

  float sha = softShadow(pos, ld1, 4.0, 0.01, 64.0);

  float dif1 = max(dot(nor,ld1),0.0);
  float spe1 = pow(max(dot(reflect(rd, nor), ld1), 0.), 10.);
  float lin1 = mix(0.2, 1.0, dif1*sha);

  float dif2 = max(dot(nor,ld2),0.0);
  float spe2 = pow(max(dot(reflect(rd, nor), ld2), 0.), 10.);
  float lin2 = mix(0.1, 1.0, dif1*sha);

  vec3 col = vec3(0.0);
  col += sunColor1*lin1*color;
  col += sunColor2*lin2*color;
  col *= 0.5;
  col += sunColor1*spe1*sha;
  col += sunColor2*spe2*sha;
  
  const float minds = 2.0;
  col = mix(mix(0.1*col, col, 1.0-mind), col, tanh_approx(minds*abs(minda*minds))/minds);

  col = col*ifade;
  
  col = mix(col, glowCol, sd);
  
  return col;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 q=fragCoord.xy/iResolution.xy; 
  vec2 p = -1.0 + 2.0*q;
  p.x *= iResolution.x/iResolution.y;

  vec3 ro = vec3(3, 3.0, -9.0);
  vec3 la = center;
  vec3 up = vec3(0.0, 1.0, 0.0);
  
  switch(PERIODN) {
  case 0:
    ro *= 0.25+0.05*PERIODTIME/PERIOD;
    ro.xz *= ROT(PERIODTIME/PERIOD);
    break;
  case 1:
    ro *= 1.0-0.6*PERIODTIME/PERIOD;
    ro.xz *= ROT(1.0+PERIODTIME/PERIOD);
    ro.xy *= ROT(-0.5*PERIODTIME/PERIOD);
    break;
  case 2:
    ro = vec3(5.0-15.0*PERIODTIME/PERIOD, -3.0+6.0*PERIODTIME/PERIOD, 30.0-40.0*PERIODTIME/PERIOD);
    break;
  default:
    break;
  }
  
  vec3 ww = normalize(la - ro);
  vec3 uu = normalize(cross(up, ww));
  vec3 vv = normalize(cross(ww,uu));
  vec3 rd = normalize(p.x*uu + p.y*vv + 2.5*ww );

  vec3 col = render(ro, rd);
  
  col *= smoothstep(0.0, FADE, PERIODTIME);
  col *= 1.0-smoothstep(PERIOD-FADE, PERIOD, PERIODTIME);

  fragColor = vec4(postProcess(col, q),1.0);
}
'''


class StMengerSpongeSpaceBase(GlslSource):
    name = 'st_menger_sponge_space_base'
    label = 'Menger sponge space base'
    description = 'License CC0: Menger sponge space base Tweaking menger sponges revealed a space based hidden inside'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
