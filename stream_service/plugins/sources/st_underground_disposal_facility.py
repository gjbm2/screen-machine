"""Underground disposal facility: okota shader 0306

Source: https://www.shadertoy.com/view/3tGBDV
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

#define time iTime
float bpm = 140.;
float PI = acos(-1.);

float random (in vec2 st) {return fract(sin(dot(st.xy,vec2(12.9898,78.233)))*43758.5453123);}

float noise (in vec2 st) {
    vec2 i = floor(st);
    vec2 f = fract(st);

    float a = random(i);
    float b = random(i + vec2(1.0, 0.0));
    float c = random(i + vec2(0.0, 1.0));
    float d = random(i + vec2(1.0, 1.0));

    vec2 u = f * f * (3.0 - 2.0 * f);

    return mix(a, b, u.x) +
            (c - a)* u.y * (1.0 - u.x) +
            (d - b) * u.x * u.y;
}

#define OCTAVES 6
float fbm (in vec2 st) {
st *= 10.;
    float value = 0.0;
    float amplitude = .5;
    float frequency = 0.;
    for (int i = 0; i < OCTAVES; i++) {
        value += amplitude * noise(st);
        st *= 2.;
        amplitude *= .5;
    }
    return value*0.45;
}


mat2 rot(float r){
  return mat2(cos(r),sin(r),-sin(r),cos(r));
  }
  
  vec2 pmod(vec2 p,float n){
    float np = 2.0*PI/n;
    float r = atan(p.x,p.y)-0.5*np;
    r = mod(r,np)-0.5*np;
    return length(p)*vec2(cos(r),sin(r));
    }

    float dist(vec3 p){
      vec3 sp = p;
      p.xz = mod(p.xz,2.)-1.;
       p.y = mod(p.y,10.)-5.;
      p.xz = pmod(p.xz,4.);
      float scale = 1.0;
      for(int i = 0;i<10;i++){
        p = abs(p)-vec3(0.4,2.0,0.);
        float r2 = dot(p,p);
        float sc = 2.0/clamp(r2,0.4,1.0);
        scale *= sc;
        p *= sc;
        p -= vec3(0.35,1.0,0.35);
        p.xz *= rot(0.25*PI);
        }
        float ssx = (1.0+0.1*length(p.xz)*pow(abs(sin(sp.y*5.+2.*time)),6.));
      
      
        float d= length(p)/scale-0.002*ssx;
      return d;
      } 
    
      vec3 gn(vec3 p){
        vec2 e = vec2(0.001,0.);
        return normalize(vec3(
        dist(p+e.xyy)-dist(p-e.xyy),
        dist(p+e.yxy)-dist(p-e.yxy),
        dist(p+e.yyx)-dist(p-e.yyx)
        ));
        
        
        }
      

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // Normalized pixel coordinates (from 0 to 1)
    vec2 uv = fragCoord/iResolution.xy;

    // Time varying pixel color
    vec3 col =vec3(0);
 vec2 p = uv-0.5;
    p.x *= iResolution.x/iResolution.y;
  p.y += 0.1*fbm(vec2(0.0,5.*time));
    vec3 of = vec3(3.0,0.2*time,0.2);
  vec3 ta =vec3(0)-of;
  vec3 ro = vec3(1,0,0)-of;
float t  = 0.;
  float fov = 0.8;
  float frag = 0.0;
  float sty = fract(0.125*0.125*time*bpm/60.);
  if(sty>0.75){
    fov = 0.4;
    of = vec3(0.24,-0.2*time,10);
    ta = vec3(0)-of;
    float rsa = 0.15;
    float ksst = 0.2*time;
    ro = vec3(rsa*cos(ksst),1.,rsa*sin(ksst))-of;
    t= 0.1;
    }else
    if(sty>0.5){
              
    of = vec3(0,-3.,0.3*time);
    ta = vec3(0)-of;
    float rsa = 0.1;
    float ksst = 0.;
    ro = vec3(rsa*cos(ksst),0.,rsa*sin(ksst))-of;
      t = 0.;
      }else if(sty>0.25){
        fov = 1.5;
        of = vec3(0,0.1*time,0);
        ta= vec3(0)-of;
        ro = vec3(1,0,0)-of;
        frag = 1.0;
        }


  
  vec3 cdir = normalize(ta-ro);
  vec3 side = cross(cdir,vec3(0,1,0));
  vec3 up = cross(side,cdir);
  
  vec3 rd = normalize(p.x*side+p.y*up+cdir*fov);
  if(frag == 1.0) rd.yz*= rot(0.5*PI);
  float d;
  float ac = 0.;
  float ep = 0.001;
  for(int i = 0;i<77;i++){
    d =dist(ro+rd*t);
    t += d;
    ac += exp(-3.0*d);
    if(d<ep)break;
  }

  if(d<ep){
    vec3 ld = normalize(vec3(1));
   vec3 sp = ro+rd*t;
    vec3 normal = gn(sp);
    
    float sca = 10.;
    float noiy =fbm(sp.xz*sca);
    float noiz = fbm(sp.xy*sca);
    float noibound = smoothstep(0.03,0.05,noiy*noiz);
    
    float scb = sca*50.;
    float noix2 = fbm(sp.yz*scb);
    float noiy2 = fbm(sp.xz*scb);
    float noiz2 = fbm(sp.xy*scb);
    
      if(noibound<0.9)normal = normalize(normal+vec3(noix2,noiy2,noiz2));

    float alp = 0.5;
    float NdotL = max(dot(normal,ld),0.);
    vec3 diff = 0.4*vec3(pow(NdotL*alp+1.-alp,2.0));
    vec3 df = diff;
    diff = mix(diff*vec3(0.9,0.4,0.1),diff,noibound);
    if(noibound<1.)diff = mix(df*vec3(0.9,0.2,0.1),diff,smoothstep(0.03,0.05,noiy2*noiz2));
    if(noiy*noiz>0.03&&noiy*noiz<0.038) diff *= 0.;
    vec3 R = reflect(rd,normal);
    float spec = pow(max(dot(ld,R),0.),5.0);
    if(noibound<0.9)spec *= 0.;
    col = diff+0.7*spec;
    }
  vec3 fogcol = vec3(0.1);
    float far = 3.0;
    col = mix(col,fogcol,clamp(t/far,0.0,1.0));
  float noifog = fbm(uv*0.5-time*vec2(0.05,0.1));
  float noifog2 = fbm(uv*0.5-time*vec2(-0.05,0.1));
    col += (noifog+noifog2)*0.5;
    col = pow(col,vec3(1.0,1.0,1.3));
    // Output to screen
    fragColor = vec4(col,1.0);
}'''


class StUndergroundDisposalFacility(GlslSource):
    name = 'st_underground_disposal_facility'
    label = 'Underground disposal facility'
    description = 'okota shader 0306'
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'noise'}
    params = scene_params({}, render_scale=0.25)
