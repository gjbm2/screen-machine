"""Doodle: 4 D Rendering: playing around with the raymarching equation and SDFs in 4 dimensions

Source: https://www.shadertoy.com/view/td2cDd
Author: blackle
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.

float smin(float a, float b, float k) {
  float h = max(0., k-abs(a-b))/k;
  return min(a,b)-h*h*h*k/6.;
}

//smooth triangle wave for smooth domain repetition https://www.desmos.com/calculator/ototv6tja8
vec4 stri(vec4 p, float k) {
  return asin(sin(p*3.14)*k)/3.14+0.5;
}

float scene(vec4 p) {
  vec4 q = abs(p) - 1.;
  float cube = length(max(q,0.0)) + min(max(max(q.x,q.w),max(q.y,q.z)),0.0) - 0.1;
  float scale = 1.;
  vec4 p2 = p+iTime*0.2;
  p2 = (stri(p2/scale, .9)-0.5)*scale;
  float spheres = length(p2)-0.2;
  spheres = -smin(-(length(p) - 2.), -spheres, 0.1);
  return smin(cube, spheres, 0.5);
}

vec4 norm(vec4 p) {
  mat4 k = mat4(p,p,p,p) - mat4(0.001);
  return normalize(scene(p) - vec4( scene(k[0]),scene(k[1]),scene(k[2]),scene(k[3]) ) );
}

vec3 erot(vec3 p, vec3 ax, float ro) {
  return mix(dot(ax,p)*ax,p,cos(ro)) + sin(ro)*cross(ax,p);
}

vec3 srgb(float r, float g, float b) {
  return pow(vec3(r,g,b),vec3(2.));
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
  vec2 uv = (fragCoord.xy - 0.5*iResolution.xy)/iResolution.y;
  vec2 mouse = (iMouse.xy - 0.5*iResolution.xy)/iResolution.y;

  vec4 cam = normalize(vec4(1,uv,0));
  vec4 init = vec4(-5,0,0,sin(iTime*0.1));

  float wrot = cos(iTime*0.5);
  float zrot = cos(iTime*0.25);
  float yrot = sin(iTime*0.5);
  float zrot2 = iTime;
  if (iMouse.z > 0.) {
    zrot = mouse.x*2.;
    wrot = radians(45.0);
    yrot = mouse.y*2.;
    zrot2 = 0.;
  }
  cam.xyw = erot(cam.xyw, vec3(0,1,0), zrot);
  init.xyw = erot(init.xyw, vec3(0,1,0), zrot);
  cam.xyz = erot(cam.xyz, vec3(0,1,0), yrot);
  init.xyz = erot(init.xyz, vec3(0,1,0), yrot);
  cam.yzw = erot(cam.yzw, vec3(0,1,0), wrot);
  init.yzw = erot(init.yzw, vec3(0,1,0), wrot);
  cam.xyz = erot(cam.xyz, vec3(0,0,1), zrot2);
  init.xyz = erot(init.xyz, vec3(0,0,1), zrot2);
  
  vec4 p = init;
  bool hit = false;
  for (int i = 0; i<200 && !hit;i++) {
    float dist = scene(p);
    hit = dist*dist < 1e-6;
    p+=dist*cam;
  }
  vec4 n = norm(p);
  vec4 r = reflect(cam,n);
  vec4 aon = reflect(cam, norm(p+r*0.3));
  float factor = length(sin(aon*3.)*0.5+0.5)/2.;
  vec3 color = mix(srgb(0.1,0.1,0.2), srgb(0.2,0.6,0.9), factor) + pow(factor, 10.);
  fragColor.xyz = hit ? color : srgb(0.1,0.1,0.1);
  fragColor.xyz = sqrt(fragColor.xyz);
}'''


class StDoodle4DRendering(GlslSource):
    name = 'st_doodle_4_d_rendering'
    label = 'Doodle: 4 D Rendering'
    description = 'playing around with the raymarching equation and SDFs in 4 dimensions'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
