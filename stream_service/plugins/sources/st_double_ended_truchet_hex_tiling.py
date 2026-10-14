"""Double ended truchet hex tiling: License CC0: Double ended truchet hextiling Decided to experiment a bit with double ended truchet with triangle tiles stacked inside hex tiles. Inspired by...

Source: https://www.shadertoy.com/view/wtXBz2
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Double ended truchet hextiling
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License CC0: Double ended truchet hextiling
//  Decided to experiment a bit with double ended truchet with triangle tiles stacked inside hext iles.
//  Inspired by work done by BigWings and Shane
    
#define PI              3.141592654
#define TAU             (2.0*PI)
#define TIME            iTime
#define RESOLUTION      iResolution
#define ORT(p)          vec2((p).y, -(p).x)

const vec2 sz       = vec2(1.0, sqrt(3.0));
const vec2 hsz      = 0.5*sz;
const float is3     = 1.0/sqrt(3.0);
const vec2 cdir     = normalize(vec2(1.0, is3));
const vec2 flipy    = vec2(1.0, -1.0);

const vec2 coords[6] = vec2[6](
  is3*cdir*1.0/3.0,
  is3*cdir*2.0/3.0,
  vec2(0.5, is3/6.0),
  vec2(0.5, -is3/6.0),
  is3*cdir*2.0/3.0*flipy,
  is3*cdir*1.0/3.0*flipy
  );

const vec2 dcoords[6] = vec2[6](
  ORT(cdir),
  ORT(cdir),
  vec2(-1.0, 0.0),
  vec2(-1.0, 0.0),
  ORT(-cdir*flipy),
  ORT(-cdir*flipy)
  );

const int noCorners = 15;
const int corners[15*6] = int[15*6](
  0, 1, 2, 3, 4, 5, 
  0, 1, 2, 4, 3, 5, 
  0, 1, 2, 5, 3, 4, 
  0, 2, 1, 3, 4, 5, 
  0, 2, 1, 4, 3, 5, 
  0, 2, 1, 5, 3, 4, 
  0, 3, 1, 2, 4, 5, 
  0, 3, 1, 4, 2, 5, 
  0, 3, 1, 5, 2, 4, 
  0, 4, 1, 2, 3, 5, 
  0, 4, 1, 3, 2, 5, 
  0, 4, 1, 5, 2, 3, 
  0, 5, 1, 2, 3, 4, 
  0, 5, 1, 3, 2, 4, 
  0, 5, 1, 4, 2, 3
  );

float hash(vec3 r)  { 
  return fract(sin(dot(r.xy,vec2(1.38984*sin(r.z),1.13233*cos(r.z))))*653758.5453); 
}

vec2 hextile(inout vec2 p) {
  // See Art of Code: Hexagonal Tiling Explained!
  // https://www.youtube.com/watch?v=VmrIDyYiJBA

  vec2 p1 = mod(p, sz)-hsz;
  vec2 p2 = mod(p - hsz, sz)-hsz;
  vec2 p3 = mix(p2, p1, vec2(dot(p1, p1) < dot(p2, p2)));
  vec2 n = ((p3 - p + hsz)/sz);
  p = p3;

  // Rounding to make hextile 0,0 well behaved
  return round(n*2.0)/2.0;
}

vec2 toPolar(vec2 p) {
  return vec2(length(p), atan(p.y, p.x));
}

vec2 toRect(vec2 p) {
  return p.x*vec2(cos(p.y), sin(p.y));
}

// https://mercury.sexy/hg_sdf/
float mod1(inout float p, float size) {
  float halfsize = size*0.5;
  float c = floor((p + halfsize)/size);
  p = mod(p + halfsize, size) - halfsize;
  return c;
}

vec3 alphaBlend(vec3 back, vec4 front) {
  vec3 colb = back.xyz;
  vec3 colf = front.xyz;
  vec3 xyz = mix(colb, colf.xyz, front.w);
  return xyz;
}

float dot2(vec2 v) { return dot(v,v); }
    
// IQ Bezier: https://www.shadertoy.com/view/MlKcDD
float bezier(vec2 pos, vec2 A, vec2 B, vec2 C) {    
  const float sqrt3 = sqrt(3.0);
  vec2 a = B - A;
  vec2 b = A - 2.0*B + C;
  vec2 c = a * 2.0;
  vec2 d = A - pos;

  float kk = 1.0/dot(b,b);
  float kx = kk * dot(a,b);
  float ky = kk * (2.0*dot(a,a)+dot(d,b))/3.0;
  float kz = kk * dot(d,a);      

  float res = 0.0;

  float p = ky - kx*kx;
  float p3 = p*p*p;
  float q = kx*(2.0*kx*kx - 3.0*ky) + kz;
  float h = q*q + 4.0*p3;

  if(h>=0.0) {   // 1 root
      h = sqrt(h);
      vec2 x = (vec2(h,-h)-q)/2.0;
      vec2 uv = sign(x)*pow(abs(x), vec2(1.0/3.0));
      float t = clamp(uv.x+uv.y-kx, 0.0, 1.0);
      res = dot2(d+(c+b*t)*t);
  } else {   // 3 roots
      float z = sqrt(-p);
      float v = acos(q/(p*z*2.0))/3.0;
      float m = cos(v);
      float n = sin(v)*sqrt3;
      vec3  t = clamp(vec3(m+m,-n-m,n-m)*z-kx, 0.0, 1.0);
      res = min(dot2(d+(c+b*t.x)*t.x), dot2(d+(c+b*t.y)*t.y));
      // the third root cannot be the closest. See https://www.shadertoy.com/view/4dsfRS
      // res = min(res,dot2(d+(c+b*t.z)*t.z));
  }
  
  return sqrt(res);
}

float bezier2(vec2 p, float f, vec2 p0, vec2 dp0, vec2 p1, vec2 dp1) {
  float dist = length(p0 - p1);
  float hdist = 0.5*f*dist;
  vec2 mp0 = p0 + hdist*dp0;
  vec2 mp1 = p1 + hdist*dp1;
  vec2 jp = (mp0 + mp1)*0.5;
  float d0 = bezier(p, p0, mp0, jp);
  float d1 = bezier(p, p1, mp1, jp);
  
  float d = d0;
  d = min(d, d1);
  return d;
}

vec3 color(vec2 p, float s, float aa, vec3 col) {
  vec2 hp = p/s;
  vec2 hn = hextile(hp);
  
  vec2 pp = toPolar(hp);
  float pn = mod1(pp.y, TAU/6.0);
  vec2 tp = toRect(pp);

  pn = mod(pn+3.0, 6.0);
  vec3 nn = vec3(hn, pn);

  float r = hash(nn);
  int sel = int(float(noCorners)*r);
  int off = sel*6;

  const vec3 scol = vec3(0.25);
  const vec3 bcol = vec3(1.0);
  const float sw = 0.05;

  for (int i = 0; i < 3; ++i) {
    int c0 = corners[off + i*2 + 0];
    int c1 = corners[off + i*2 + 1];
    
    int c = max(c0, c1) - min(c0, c1);
    
    vec2 p0 = coords[c0];
    vec2 p1 = coords[c1];

    vec2 dp0 = dcoords[c0];
    vec2 dp1 = dcoords[c1];
    
    float mi = 0.5;    
    float mx = 0.5;    
    
    float rr = fract(r*27.0*float(i+1));
    switch(c) {
      case 1:
        mx = 1.75;
        break;
      case 2:
        mx = .95;
        break;
      case 3:
        mx = 1.5;
        break;
      case 4:
        mx = 0.75;
        break;
      case 5:
        mx = 1.95;
        break;
      default:
        break;
    }
    
    float f = mix(mi, mx, rr);
    
    float dd = (bezier2(tp, f, p0, dp0, p1, dp1)-0.01)*s;
    
    vec4 sc = vec4(scol, smoothstep(-sw, sw, -dd));
    vec4 bc = vec4(bcol, smoothstep(-aa, aa, -dd));

    col = alphaBlend(col, sc);
    col = alphaBlend(col, bc);
  }
  
  return col;
}

vec3 effect(vec2 p, vec2 q) {
  float s = 0.45;
  float aa = 2.0/RESOLUTION.y;
  vec3 n;
  vec3 col = vec3(0.0);
  p += TIME*0.1;
  col = color(p, s, aa, col);

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


class StDoubleEndedTruchetHexTiling(GlslSource):
    name = 'st_double_ended_truchet_hex_tiling'
    label = 'Double ended truchet hex tiling'
    description = 'License CC0: Double ended truchet hextiling Decided to experiment a bit with double ended truchet with triangle tiles stacked inside hex tiles. Inspired by...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
