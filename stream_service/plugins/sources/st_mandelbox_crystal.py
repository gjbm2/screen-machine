"""Mandelbox crystal: License CC0: Mandelbox crystal Was reminded by some great additions lately that refraction is pretty cool to play around with

Source: https://www.shadertoy.com/view/7sj3zw
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Mandelbox crystal
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Mandelbox crystal
//  Was reminded by some great additions lately that refraction is pretty
//  cool to play around with

// Uncomment for a different "skybox"
//#define SKY_VARIANT

#define TOLERANCE         0.0001
#define MAX_RAY_LENGTH    8.0
#define MAX_RAY_MARCHES   100
#define TIME              iTime
#define RESOLUTION        iResolution
// SABS by ollij
#define LESS(a,b,c)       mix(a,b,step(0.,c))
#define SABS(x,k)         LESS((.5/(k))*(x)*(x)+(k)*.5,abs(x),abs(x)-(k))
#define ROT(a)            mat2(cos(a), sin(a), -sin(a), cos(a))
#define PI                3.141592654
#define TAU               (2.0*PI)
#define L2(x)             dot(x, x)

#define PERIOD            30.0
#define FADE              3.0
#define TIMEPERIOD        mod(TIME,PERIOD)
#define NPERIOD           floor(TIME/PERIOD)
#define PSIN(x)           (0.5 + 0.5*sin(x))

#define RAYSHAPE(ro, rd)  raySphere4(ro, rd, 0.5)
#define IRAYSHAPE(ro, rd) iraySphere4(ro, rd, 0.5)
#ifdef SKY_VARIANT
#define SKYCOLOR(ro, rd)  skyColor1(ro, rd)
#else
#define SKYCOLOR(ro, rd)  skyColor0(ro, rd)
#endif

const float fixed_radius2 = 1.8;
const float min_radius2   = 0.5;
const vec4  folding_limit = vec4(1.0);
const float scale         = -2.9-0.2;
const mat2  rot0          = ROT(0.0);
const float miss          = 1E4;
const float refrIndex     = 0.85;
const vec3  lightPos      = 2.0*vec3(1.5, 2.0, 1.0);
const float boundingSphere= 4.0;
const float dfZoom        = 1.0/8.0;
const vec3  glowCol       = vec3(3.0, 2.0, 1.);
const vec3 skyCol1        = vec3(0.2, 0.4, 0.6);
const vec3 skyCol2        = vec3(0.4, 0.7, 1.0);
const vec3 sunCol         =  vec3(8.0,7.0,6.0)/8.0;

float g_rand              = 0.5;
mat2  g_rotb              = rot0;
mat2  g_rotc              = rot0;
mat2  g_rotd              = rot0;

float saturate(float a) { return clamp(a, 0.0, 1.0); }

float hash(float co) {
  co += 6.0;
  return fract(sin(co*12.9898) * 13758.5453);
}

float tanh_approx(float x) {
//  return tanh(x);
  float x2 = x*x;
  return clamp(x*(27.0 + x2)/(27.0+9.0*x2), -1.0, 1.0);
}

// IQ's smooth min
float pmin(float a, float b, float k) {
  float h = clamp(0.5+0.5*(b-a)/k, 0.0, 1.0);
  
  return mix(b, a, h) - k*h*(1.0-h);
}

vec4 pmin(vec4 a, vec4 b, vec4 k) {
  vec4 h = clamp( 0.5+0.5*(b-a)/k, 0.0, 1.0);
  return mix(b, a, h) - k*h*(1.0-h);
}

float sphere(vec3 p, float r) {
  return length(p) - r;
}

// IQ's box
float box(vec4 p, vec4 b) {
  vec4 q = abs(p) - b;
  return length(max(q,0.0)) + min(max(max(q.x, q.w),max(q.y,q.z)),0.0);
}

// IQ's ray sphere intersection
vec2 raySphere(vec3 ro, vec3 rd, vec4 s) {
    vec3 ce = s.xyz;
    float ra = s.w;
    vec3 oc = ro - ce;
    float b = dot( oc, rd );
    float c = dot( oc, oc ) - ra*ra;
    float h = b*b - c;
    if( h<0.0 ) return vec2(miss); // no intersection
    h = sqrt( h );
    return vec2( -b-h, -b+h );
}

// IQ's ray sphere density
float raySphereDensity(vec3 ro, vec3 rd, vec4 sph, float dbuffer) {
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

// From stackoverflow
vec3 hsv2rgb(vec3 c) {
  const vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
  vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
  return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}


// IQ's ray sphere 4 intersection
float raySphere4(vec3 ro, vec3 rd, float ra) {
    float r2 = ra*ra;
    vec3 d2 = rd*rd; vec3 d3 = d2*rd;
    vec3 o2 = ro*ro; vec3 o3 = o2*ro;
    float ka = 1.0/dot(d2,d2);
    float k3 = ka* dot(ro,d3);
    float k2 = ka* dot(o2,d2);
    float k1 = ka* dot(o3,rd);
    float k0 = ka*(dot(o2,o2) - r2*r2);
    float c2 = k2 - k3*k3;
    float c1 = k1 + 2.0*k3*k3*k3 - 3.0*k3*k2;
    float c0 = k0 - 3.0*k3*k3*k3*k3 + 6.0*k3*k3*k2 - 4.0*k3*k1;
    float p = c2*c2 + c0/3.0;
    float q = c2*c2*c2 - c2*c0 + c1*c1;
    float h = q*q - p*p*p;
    if (h<0.0) return miss; //no intersection
    float sh = sqrt(h);
    float s = sign(q+sh)*pow(abs(q+sh),1.0/3.0); // cuberoot
    float t = sign(q-sh)*pow(abs(q-sh),1.0/3.0); // cuberoot
    vec2  w = vec2( s+t,s-t );
    vec2  v = vec2( w.x+c2*4.0, w.y*sqrt(3.0) )*0.5;
    float r = length(v);
    return -abs(v.y)/sqrt(r+v.x) - c1/r - k3;
}

vec3 sphere4Normal(vec3 pos) {
  return normalize( pos*pos*pos );
}

float iraySphere4(vec3 ro, vec3 rd, float ra) {
  // Computes inner intersection by intersecting a reverse outer intersection
  // Perhaps IQ's ray sphere 4 supports inner intersect but I couldn't get it to work
  vec3 rro = ro + rd*ra*4.0;
  vec3 rrd = -rd;
  float rt = raySphere4(rro, rrd, ra);

  if (rt == miss) return miss;
  
  vec3 rpos = rro + rrd*rt;
  return length(rpos - ro);
}

// IQ's ray plane intersection
float rayPlane(vec3 ro, vec3 rd, vec4 p ) {
  return -(dot(ro,p.xyz)+p.w)/dot(rd,p.xyz);
}

// Based on EvilRyu's Mandelbox
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
  float rand = g_rand;
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
  const float s = dfZoom;
  float rand = g_rand;

  p /= s;

  float dbs = sphere(p, boundingSphere);
//  if (dbs > 0.5) return dbs;

  float a = fract(3.0*rand);
  vec4 pp = vec4(p.x, p.y, p.z, 2.0*a*a);

  pp.xw *= g_rotb;
  pp.yw *= g_rotc;
  pp.zw *= g_rotd;
  
  float dmb = mb(pp);
  
  float d = dmb;
  d = max(d, dbs);
  
  return d*s;
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

vec3 render0(vec3 ro, vec3 rd, out float sd, out float td) {
  int iter = 0;
  const vec4 bs = vec4(vec3(0.0), boundingSphere*dfZoom);
  const vec4 gs = vec4(vec3(0.0), boundingSphere*dfZoom*0.66);
  vec2 tbs = raySphere(ro, rd, bs);
  if (tbs == vec2(miss)) {
    td = miss;
    return vec3(0.0);
  }
  
  float t = rayMarch(ro, rd, iter);
  
  sd = raySphereDensity(ro, rd, gs, t);

  float ifade = 1.0-tanh_approx(3.0*float(iter)/float(MAX_RAY_MARCHES));

  vec3 pos = ro + t*rd;    
  vec3 nor = vec3(0.0, 1.0, 0.0);
  
  vec3 color = vec3(0.0);

  float h = g_rand;
  
  if (t < MAX_RAY_LENGTH) {
    // Ray intersected object
    nor       = normal(pos);
    vec3 hsv  = (vec3(fract(h - 0.6 + 0.4+0.25*t), 1.0-ifade, 1.0));
    color     = hsv2rgb(hsv);
    td        = t;
  } else {
    // Ray intersected sky
    td        = miss;
    return vec3(0.0);
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

    
  return col*ifade;
}

vec3 skyColor0(vec3 ro, vec3 rd) {
  const vec3 sunDir = normalize(lightPos);
  float sunDot = max(dot(rd, sunDir), 0.0);  
  vec3 final = vec3(0.);

  final += 0.5*sunCol*pow(sunDot, 20.0);
  final += 4.0*sunCol*pow(sunDot, 400.0);    

  float tp  = rayPlane(ro, rd, vec4(vec3(0.0, 1.0, 0.0), 0.75));

  if (tp > 0.0) {
    // Ray intersected plane
    vec3 pos  = ro + tp*rd;
    vec3 nor = vec3(0.0, 1.0, 0.0);
    vec2 pp = pos.xz*1.5;
    float m = 0.5+0.25*(sin(3.0*pp.x+TIME*2.1)+sin(3.3*pp.y+TIME*2.0));
    m *= m;
    m *= m;
    pp = fract(pp+0.5)-0.5;
    float dp = pmin(abs(pp.x), abs(pp.y), 0.025);
    vec3 hsv = vec3(0.4+mix(0.15,0.0, m), tanh_approx(mix(50.0, 10.0, m)*dp), 1.0);
    vec3 col = 1.5*hsv2rgb(hsv)*exp(-mix(30.0, 10.0, m)*dp);
    float f = exp(-20.0*(max(tp-3.0, 0.0) / MAX_RAY_LENGTH));
    return mix(final, col , f);
  } else {
    // Ray intersected sky
    return final;
  }
}

vec3 skyColor1(vec3 ro, vec3 rd) {
  const vec3 sunDir = normalize(lightPos);
  float sunDot = max(dot(rd, sunDir), 0.0);  
  vec3 final = vec3(0.);

  final += mix(skyCol1, skyCol2, rd.y);
  final += 0.5*sunCol*pow(sunDot, 20.0);
  final += 4.0*sunCol*pow(sunDot, 400.0);    

  float tp  = rayPlane(ro, rd, vec4(vec3(0.0, 1.0, 0.0), 0.75));
  if (tp > 0.0) {
    vec3 pos  = ro + tp*rd;
    vec3 ld   = normalize(lightPos - pos);
    float ts4 = RAYSHAPE(pos, ld);
    vec3 spos = pos + ld*ts4;
    float its4= IRAYSHAPE(spos, ld);
    // Extremely fake soft shadows
    float sha = ts4 == miss ? 1.0 : (1.0-1.0*tanh_approx(its4*1.5/(0.1+1.0*ts4)));
    vec3 nor  = vec3(0.0, 1.0, 0.0);
    vec3 icol = 1.5*skyCol1 + 4.0*sunCol*sha*dot(-rd, nor);
    vec2 ppos = pos.xz*0.75;
    ppos = fract(ppos+0.5)-0.5;
    float pd  = min(abs(ppos.x), abs(ppos.y));
    vec3  pcol= mix(vec3(0.4), vec3(0.3), exp(-60.0*pd));

    vec3 col  = icol*pcol;
    col = clamp(col, 0.0, 1.25);
    float f   = exp(-10.0*(max(tp-10.0, 0.0) / 100.0));
    return mix(final, col , f);
  } else{
    return final;
  }
}


vec3 render1(vec3 ro, vec3 rd) {
  const float eps  = 0.001;
  vec3 ipos = ro + eps*rd;
  vec3 ird  = rd;
  float isd = 0.0;
  float itd = 0.0;
  vec3 col = vec3(0.0);
  
  const float scaleUp = 2.0;
  
  for (int i = 0; i < 3; ++i) {
    float rtd = miss;
    float rsd;
    col = scaleUp*render0(ipos, ird, rsd, rtd);
    isd += rsd;
    if (rtd < miss) { 
      itd += rtd; 
      break;
    }
    
    float its4  = IRAYSHAPE(ipos, ird);
    itd         += its4;
    vec3 nipos  = ipos + ird*its4;
    vec3 inor   = -sphere4Normal(nipos);
    vec3 irefr  = refract(ird, inor, 1.0/refrIndex);
    if (irefr == vec3(0.0)) {
      ipos = nipos;
      ird  = reflect(ird, inor);
    } else {
      vec3 rskyCol= SKYCOLOR(ipos, irefr);
      col = rskyCol;
      break;
    }
  }
  float h = g_rand;
  float t = 0.2;
  vec3 hsv  = vec3(fract(h - 0.6 + 0.4+0.25*t), 0.3, 1);
  vec3 glowCol = hsv2rgb(hsv);

  isd = h > 0.5 ? isd : 0.0;
  col = mix(col, 2.0*glowCol, isd);
  
  col *= exp(mix(-vec3(2.0, 3.0, 4.0).zyx, vec3(0.0), tanh_approx(3.0*isd))*itd);
  
  return col;
}

vec3 render(vec3 ro, vec3 rd) {
  vec3 skyCol = SKYCOLOR(ro, rd);
  vec3 col = vec3(0.0);

  float t   = 1E6;
  float ts4 = RAYSHAPE(ro, rd);
  if (ts4 < miss) {
    t = ts4;
    vec3 pos  = ro + ts4*rd;
    vec3 nor  = sphere4Normal(pos);
    vec3 refr = refract(rd, nor, refrIndex);
    vec3 refl = reflect(rd, nor);
    vec3 rcol = SKYCOLOR(pos, refl);
    float fre = mix(0.0, 1.0, pow(1.0-dot(-rd, nor), 4.0));

    vec3 lv   = lightPos - pos;
    float ll2 = L2(lv);
    float ll  = sqrt(ll2);
    vec3 ld   = lv / ll;

    float dm  = min(1.0, 40.0/ll2);
    float dif = pow(max(dot(nor,ld),0.0), 8.0)*dm;
    float spe = pow(max(dot(reflect(-ld, nor), -rd), 0.), 100.);
    float l   = dif;

    float lin = mix(0.0, 1.0, l);
    const vec3 lcol = 2.0*sqrt(sunCol);
    col = render1(pos, refr);
    vec3 diff = hsv2rgb(vec3(0.7, fre, 0.075*lin))*lcol;
    col += fre*rcol+diff+spe*lcol;
    if (refr == vec3(0.0)) {
      // Not expected to happen as the refraction index < 1.0
      col = vec3(1.0, 0.0, 0.0);
    }
    
  } else {
    // Ray intersected sky
    return skyCol;
  }

  return col;
}
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 q=fragCoord.xy/RESOLUTION.xy; 
  vec2 p = -1.0 + 2.0*q;
  p.x *= RESOLUTION.x/RESOLUTION.y;

  float rand = hash(NPERIOD);
  g_rand = rand;

  float a = fract(3.0*rand);
  const float aa = PI/4.0;
  const float bb = PI/4.0-aa*0.5;
  float b = bb+aa*fract(5.0*rand);
  float c = bb+aa*fract(7.0*rand);
  float d = bb+aa*fract(13.0*rand);
  g_rotb = ROT(b);
  g_rotc = ROT(c);
  g_rotd = ROT(d);


  vec3 ro = 0.6*vec3(2.0, 0, 0.2)+vec3(0.0, 0.75, 0.0);
  ro.xz *= ROT((TIME*0.05));
  ro.yz *= ROT(sin(TIME*0.05*sqrt(0.5))*0.5);

  vec3 ww = normalize(vec3(0.0, 0.0, 0.0) - ro);
  vec3 uu = normalize(cross( vec3(0.0,1.0,0.0), ww));
  vec3 vv = normalize(cross(ww,uu));
  float rdd = 2.0+0.5*tanh_approx(length(p));
//  rdd = 2.0;
  vec3 rd = normalize( p.x*uu + p.y*vv + rdd*ww);

  vec3 col = render(ro, rd);
  col = clamp(col, 0.0, 1.0);
  col *= smoothstep(0.0, FADE, TIMEPERIOD);
  col *= 1.0-smoothstep(PERIOD-FADE, PERIOD, TIMEPERIOD);
  fragColor = vec4(postProcess(col, q),1.0);
}
'''


class StMandelboxCrystal(GlslSource):
    name = 'st_mandelbox_crystal'
    label = 'Mandelbox crystal'
    description = 'License CC0: Mandelbox crystal Was reminded by some great additions lately that refraction is pretty cool to play around with'
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'music'}
    params = scene_params({}, render_scale=0.25)
