"""Jewel room: 2 hour coding https://www.youtube.com/watch?v=LlR4BXY3cWg

Source: https://www.shadertoy.com/view/stfGDn
Author: butadiene
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //Released under the MIT license
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//Copyright (c) 2021 Butadiene
//Released under the MIT license
//https://opensource.org/licenses/mit-license.php

#define T iTime

const float PI = 3.141592;

mat2 rot(float r){
  return mat2(cos(r),sin(r),-sin(r),cos(r));
}

float box2d(vec2 p,vec2 s){
  vec2 q = abs(p);
  vec2 m = max(s-q,0.);
  return length(max(q-s,0.))-min(m.x,m.y);
}

float crobox(vec3 p,float sca){
  float dx = box2d(p.yz,vec2(sca));
  float dy = box2d(p.xz,vec2(sca));
  float dz = box2d(p.xy,vec2(sca));
  return min(min(dx,dy),dz);
}

float box(vec3 p,vec3 s){
  vec3 q = abs(p);
  vec3 m = max(s-q,0.);
  return length(max(q-s,0.))-min(m.z,min(m.x,m.y));
}

float ease(float t,float k){
  return -exp(-1.0*mod(t,k))+floor(t/k);
}

vec4 nanika(vec3 p){
  float kt =T;/// 2.*ease(time*2.,3.);
  p.xz *= rot(kt);
  p.yz *= rot(kt);
  vec3 acol = vec3(0.);
  float sct = 0.23;
  vec3 col1 = vec3(0.6,0.6,0.2);
  vec3 col2 = vec3(0.2,0.6,0.6);
  vec3 col3 = vec3(0.6,0.6,0.2);
  for(int i = 0;i<8;i++){
    p = abs(p)-0.2;
    p.xy *= rot(sct);
    p.xz *= rot(sct);
    p.yz *= rot(sct);
    if(p.x<p.y&&p.x<p.z){acol += col1;}
    else if(p.y<p.z){acol += col2;}
    else{acol += col3;}
  }
  
  float d = length(p)-0.2;
  d = crobox(p,0.05);
  vec3 col =1.*acol*exp(-3.0*d);
  return vec4(col,d);
}

vec4 yuka(vec3 p,float t){
  p.y -= 0.;
  p.z += T*10.;
  float k = 1.5;
  float sc = k*40.;
  float rsc = 0.2;
  
  p.y = -abs(p.y);
  
  float xzmod = 80.0;
  p.xz = mod(p.xz,xzmod)-0.5*xzmod;
  vec3 acol = vec3(0);
  for(int i = 0;i<7;i++){
    p.xz = abs(p.xz)-sc;
    sc *= 0.5;
    p.xy *= rot(rsc);
    p.zy *= rot(rsc);
    p.xz *= rot(0.3);
    if(p.x<p.z){ acol += vec3(0.2,0.6,0.8);}
    else{acol += vec3(0.6,0.2,0.5); }
  }
  

  float size = k*0.5-0.1;
  float d = box(p,vec3(size,10.,size));
  vec3 col = vec3(0);
  return vec4(2.2*acol*exp(-1.0*d)*exp(-0.08*t),d);
}

vec4 dist(vec3 p,float totalt){
  float k = 0.6;
  //p = mod(p,k)-0.5*k;
  float scy = 1.0;
  vec4 yukad = yuka(p*scy,totalt)/scy;
  float scn = 0.6;
  vec4 nanikad = nanika(p*scn);
  float d = min(nanikad.w/scn,yukad.w);
  vec3 col = nanikad.xyz;
  col += yukad.xyz;
  return vec4(col,d);
}

vec3 gn(vec3 p){
  vec2 e = vec2(0.001,0.);
  return normalize(vec3(
    dist(p+e.xyy,1.).w-dist(p-e.xyy,1.).w,
    dist(p+e.yxy,1.).w-dist(p-e.yxy,1.).w,
    dist(p+e.yyx,1.).w-dist(p-e.yyx,1.).w
    ));
}

void mainImage( out vec4 fragColor, in vec2 fragCoord ){vec2 r=iResolution.xy,p=(fragCoord.xy*2.-r)/min(r.x,r.y);
vec2 uv = gl_FragCoord.xy/r;

float ra = 14.0;
float kt = T*0.3;
vec3 ro = vec3(ra*cos(kt),6.,ra*sin(kt));
vec3 ta = vec3(0);

vec3 cdir = normalize(ta-ro);
vec3 side = cross(cdir,vec3(0,1,0));
vec3 up = cross(side,cdir);
vec3 rd = normalize(side*p.x+cdir*0.8+up*p.y);

float d,t= 0.;
float es = 0.0001;
vec3 ac = vec3(0.);
for(int i = 0;i<76;i++){
  vec4 rsd = dist(ro+rd*t,t);
  d = rsd.w;
  t += d;
  ac += rsd.xyz;
  if(d<es)break;
}

vec3 col = vec3(0);
col = ac*0.01;

if(yuka(ro+rd*t,1.).w<es){
  vec3 sro =ro;
  vec3 srd =rd;
  vec3 sp = ro+rd*t;
  vec3 normal = gn(sp);
  rd = reflect(rd,normal);
  ro = sp;
  t = 0.01;
  ac = vec3(0.);
  for(int i = 0;i<36;i++){
    vec4 rsd = dist(ro+rd*t,length(rd*t+sp));
    d = rsd.w;
    t += d;
    
    ac += rsd.xyz;
    if(d<es)break;
  }
  col += 0.01*ac;
}



float et = 0.1/abs(uv.y*4.-ease(uv.x*16.,4.));
col = 1.2*pow(col,vec3(1.2));
fragColor=vec4(col,1);}


'''


class StJewelRoom(GlslSource):
    name = 'st_jewel_room'
    label = 'Jewel room'
    description = '2 hour coding https://www.youtube.com/watch?v=LlR4BXY3cWg'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
