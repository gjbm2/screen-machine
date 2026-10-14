"""Twitch: PS1 Singularity Jorts: don't ask. it was very silly. watch the stream highlight here: https://www.twitch.tv/videos/523838443

Source: https://www.shadertoy.com/view/3lcGR2
Author: blackle
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.

float linedist(vec3 p, vec3 a, vec3 b) {
  float k = dot(p-a, b-a)/dot(b-a,b-a);
  return distance(p, mix(a,b,clamp(k,0.,1.)));
}

float linedist(vec2 p, vec2 a, vec2 b) {
  float k = dot(p-a, b-a)/dot(b-a,b-a);
  return distance(p, mix(a,b,clamp(k,0.,1.)));
}

float smin(float a, float b, float k) {
  float h = max(k - abs(a-b), 0.)/k;
  return min(a,b) - h*h*h*k/6.;
}

float skeleton(vec3 p) {
  float thighgap = 1.2;
  float l1 = linedist(p, vec3(0,thighgap,-1), vec3(0,thighgap,1))-1.;
  float l2 = linedist(p, vec3(0,-thighgap,-1), vec3(0,-thighgap,1))-1.;
  float waist = linedist(p, vec3(0,-1,1), vec3(0,1,1))-1.1;
  return smin(waist, min(l1,l2), 0.2);
}

float loop(vec3 p, vec2 b) {
  float c = length(p.xz*vec2(1.5,1))-b.x;
  return 0.5*(length(vec2(c,p.y))-b.y);
}

float crungy = 0.;
float scene(vec3 p) {
  //the singularity
  if (fract(iTime/15.) > 0.9) {
  p.x += sin(p.y*4.+iTime*6.)*0.1;
  p.y += sin(p.z*4.+iTime*6.)*0.1;
  }
  float skel = skeleton(p);
  float l = min(loop(p-vec3(-1.15,-1,1.1), vec2(0.2,0.05)), loop(p-vec3(-1.15,1,1.1), vec2(0.2,0.05)));
  float jort = linedist(vec2(skel, p.z), vec2(0,1.3),vec2(0,-1.3))-0.05;
  crungy = l < jort ? 1. : 0.;
  return min(l,jort);
}

vec3 norm(vec3 p) {
  mat3 k = mat3(p,p,p) - mat3(0.01);
  return normalize(scene(p) - vec3(scene(k[0]),scene(k[1]),scene(k[2])));
}

void erot(inout vec3 p, vec3 ax, float ro) {
  p = mix(dot(ax,p)*ax, p, cos(ro)) + sin(ro)*cross(ax,p);
}

float diffuse(vec3 n, vec3 l) {
  return max(dot(n, l), 0.);
}

float spec(vec3 n, vec3 c, vec3 l, float k) {
  //TODO;
  vec3 r = reflect(c, n);
  return pow(max(dot(r, l), 0.), k);
}

#define FK(k) floatBitsToInt(cos(k))^floatBitsToInt(k)
float hash(vec2 p) {
  int x = FK(p.x); int y = FK(p.y);
  return float((x*x-y)*(y*y+x)+x)/2.14e9;
}

float noise(vec2 p) {
  vec2 id = floor(p);
  vec2 co = fract(p);
  return mix(mix(hash(id),hash(id+vec2(1,0)),co.x),mix(hash(id+vec2(0,1)),hash(id+vec2(1,1)),co.x), co.y)*0.5+0.5;
}

vec2 rot(vec2 p, float ro) {
  return mat2(cos(ro),-sin(ro),sin(ro),cos(ro))*p;
}

float maze(vec2 p) {
  vec2 id = floor(p);
  vec2 co = fract(p)-0.5;
  co.y *= sign(hash(id));
  return abs(abs(co.x+co.y)-0.5)/sqrt(2.);
}

float perlin(vec2 p) {
  float avg = 0.;
  float acc = 0.;
  for (int i = 0; i < 6; i++) {
    vec2 off = vec2(hash(vec2(float(i), cos(float(i)))),hash(vec2(float(i)+1., cos(float(i)+2.))));
    float r = acos(-1.)*hash(vec2(float(i*i), cos(float(i)+4.)));
    avg += noise(rot(p,r)*float(i+1)+off*20.);
    acc += 1.;
  }
  return avg/acc;
}

vec3 denim(vec2 tex) {
  float horiz = perlin(tex*vec2(1,90));
  float vert = perlin(tex*vec2(90,1));
  float fray = max(smoothstep(-1.1,-1.2,tex.y),crungy);
  vec3 d = pow(mix(vec3(0.25,0.5,0.7),vec3(1),fray),vec3(2.5))*mix(horiz,vert,fray*0.5+0.5)*mix(0.2,1.5,perlin(rot(tex,0.2)*0.7));
  float fade = max(cos(iTime+tex.x*0.05)*3.-2.,0.);
  return mix(d, vec3(0,1,0), fade*smoothstep(0.05,0.,maze(tex*4.)));
}

vec3 shade(vec3 n, vec3 p, vec3 cam) {
  vec3 col = mix(denim(p.yz), denim(p.xz), n.y*n.y);
  n += col*0.1; // alkama's suggestion :3
  
  float diff = diffuse(n, vec3(1,0,0)) + diffuse(n, normalize(vec3(-1,1,0))) + diffuse(n, normalize(vec3(-1,0,0)))*0.5 + diffuse(n, normalize(vec3(1,-2,1)))*0.25;
  float specex = max(mix(4.,0.5,col.b*1.2-0.2),0.);
  float spec = spec(n, cam, vec3(1,0,0), specex) + spec(n, cam, normalize(vec3(-1,1,0)), specex);
  return col*diff/4. + spec*0.015;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
  vec2 uv = vec2(fragCoord.x / iResolution.x, fragCoord.y / iResolution.y);
  uv -= 0.5;
  uv /= vec2(iResolution.y / iResolution.x, 1);
  
  vec3 cam = normalize(vec3(1,uv));
  vec3 init = vec3(-5,0,0);
  
  erot(init, vec3(0,0,1), sin(iTime)*0.4);
  erot(cam, vec3(0,0,1), sin(iTime)*0.4);
  erot(init, vec3(0,1,0), cos(iTime)*0.4);
  erot(cam, vec3(0,1,0), cos(iTime)*0.4);
  vec3 p = init;
  bool hit = false;
  for (int i = 0; i < 200; i++) {
    float dist = scene(p);
    if (abs(dist) < 0.001) { hit = true; break; }
    if (distance(p,init) > 100.) break;
    p += dist*cam;
  }
  vec3 n = norm(p);
  if (hit) {
    fragColor.xyz = sqrt(shade(n, p, cam));
  } else {
    crungy = 0.;
    fragColor.xyz = sqrt(denim(uv*2.));
  }
}'''


class StTwitchPs1SingularityJorts(GlslSource):
    name = 'st_twitch_ps1_singularity_jorts'
    label = 'Twitch: PS1 Singularity Jorts'
    description = "don't ask. it was very silly. watch the stream highlight here: https://www.twitch.tv/videos/523838443"
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.4)
