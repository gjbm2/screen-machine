"""The Family Shader: today I plugged my computer to the family television and live coded a shader for my family while we all listened to music and my sister's boyfriend put on a...

Source: https://www.shadertoy.com/view/3tc3RN
Author: blackle
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.

float square(vec3 p, vec3 dim) {
  p = abs(p);
  p -= dim;
  return max(p.x,max(p.y,p.z));
}

float bpm = 117.;
#define beat fract(iTime/60.*bpm)
#define bar floor(iTime/60.*bpm)

float linedist(vec3 p, vec3 a, vec3 b) {
  float k = dot(p-a,b-a)/dot(b-a,b-a);
  return distance(p, mix(a, b, clamp(k,0.,1.)));
}

float cage(vec3 p, float r) {
  p = abs(p);
  vec3 t = (mod(bar,2.)==1.) ? vec3(0.,r,r) : vec3(r,0.,0.);
  float bst = linedist(p, t.xyz, vec3(r,r,r));
  bst = min(linedist(p, t.zxy, vec3(r,r,r)), bst);
  bst = min(linedist(p, t.yzx, vec3(r,r,r)), bst);
  return bst-0.1;
}

vec2 smin(vec2 a, vec2 b, float k){
  float h = max(k - abs(a.x-b.x),0.)/k;
  float fac = mix(a.y,b.y,smoothstep(-k/2., k/2., a.x-b.x));
  return vec2(min(a.x,b.x)-h*h*h*k/6., fac);
}

float comp(vec3 p, vec3 sc, float sz) {
  p = (fract(p*sc)-0.5)/sc;
  return length(p) - sz;
}

vec3 erot(vec3 p, vec3 ax, float ro) {
  return mix(dot(p,ax)*ax,p,cos(ro))+sin(ro)*cross(ax,p);
}

float mat = 0.;
#define time (iTime*2.)
float scene(vec3 p) {
  vec3 op = p - vec3(time,0,0);
  op = erot(op, vec3(0,1,0),iTime);
  op = erot(op, vec3(0,0,1),iTime*0.5);
  op = erot(op, vec3(1,0,0),iTime*0.25);
  
  vec3 op2 = p - vec3(time,0,0);
  op2 = erot(op, vec3(0,1,0),-iTime);
  op2 = erot(op, vec3(0,0,1),iTime*0.25);
  op2 = erot(op, vec3(1,0,0),-iTime*0.5);
  float scene =0.2;
  float sq = square(op, vec3(0.45));
  vec2 best = vec2(comp(p, vec3(0.2), 1.), 0.);
  best = smin(best, vec2(comp(p-vec3(0,0,iTime), vec3(0.2,0.2,1.2), 0.2),1.),1.);
  best = smin(best, vec2(comp(p-vec3(0,iTime,0), vec3(0.2,1.2,0.2), 0.2),1.),1.);
  best = smin(best, vec2(comp(p-vec3(iTime,0,0), vec3(1.2,0.2,0.2), 0.2),1.),1.); 
  mat = best.y;
  if (sq < best.x) { 
    mat = 2.;
    best.x = sq;
  }
  float cg = cage(op2, 1.5 + sin(iTime*7.)*0.5);
  if (cg < best.x) { 
    mat = 3.;
    best.x = cg;
  }
  return best.x;
}

vec3 norm(vec3 p) {
  mat3 k = mat3(p,p,p) - mat3(0.01);
  return normalize(scene(p) - vec3(scene(k[0]),scene(k[1]),scene(k[2])));
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
  vec2 uv = vec2(fragCoord.x / iResolution.x, fragCoord.y / iResolution.y);
  uv -= 0.5;
  uv /= vec2(iResolution.y / iResolution.x, 1);
  
  vec3 cam = normalize(vec3(1.+beat*0.1, uv));
  vec3 init = vec3(-8.+pow(cos(iTime*acos(-1.)/60.*bpm/6.),2.)*2., 0,0);
  cam = erot(cam, vec3(0,1,0),sin(iTime/2.)*0.3);
  init = erot(init, vec3(0,1,0),sin(iTime/2.)*0.3);
  cam = erot(cam, vec3(0,0,1),iTime*0.2);
  init = erot(init, vec3(0,0,1),iTime*0.2);
  
  init += vec3(time,0,0);
  vec3 p = init + cam;
  bool hit = false;
  for (int i =0; i< 28; i++) {
    float dist = scene(p);
    if (dist*dist<0.00001) { hit = true; break; }
    if (distance(p,init)>40.) break;
    p += cam*dist;
  }
  float fog = smoothstep(40.,0.,distance(init,p));
  vec3 mcol = mix(vec3(1.), vec3(0.0,0.5,0.9), 1.-mat);
  if (mat == 2.) mcol = vec3(1.,0.,0.8);
  vec3 n = norm(p);
  fragColor.xyz = mix(vec3(0.,0.3,0.5),mcol*(pow(dot(n,vec3(0,0,1))*0.5+0.5,2.)),fog);
  fragColor.xyz = smoothstep(vec3(0), vec3(1), sqrt(fragColor.xyz));
}'''


class StTheFamilyShader(GlslSource):
    name = 'st_the_family_shader'
    label = 'The Family Shader'
    description = "today I plugged my computer to the family television and live coded a shader for my family while we all listened to music and my sister's boyfriend put on a..."
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
