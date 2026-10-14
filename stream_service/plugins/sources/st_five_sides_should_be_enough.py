"""Five Sides Should Be Enough: animated version of my freestyle graphics entry for demosplash. programmed in bonzomatic in 15 minutes while waiting for my flight from toronto. set ANIMATED...

Source: https://www.shadertoy.com/view/WdGXRW
Author: blackle
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.

#define ANIMATED 1

#if ANIMATED
#define time (iTime/3.+12.6)
#else
#define time 16.
#endif

vec4 plas( vec2 v, float t )
{
  float c = 0.5 + sin( v.x * 10.0 ) + cos( sin( t + v.y ) * 20.0 );
  return vec4( sin(c * 0.2 + cos(t)), c * 0.15, cos( c * 0.1 + t / .4 ) * .25, 1.0 );
}

#define FK(k) floatBitsToInt(cos(k))^floatBitsToInt(k)
float hash (vec2 p) {
  int x = FK(p.x);int y = FK(p.y);
  return float((x*x-y)*(y*y+x)-x)/2.14e9;
}

float hash1(float k){
  return hash(vec2(k,cos(k)+1.));
}

vec3 hash3(float k) {
  float rnd = hash1(k);
  return vec3(hash1(rnd*2.),hash1(rnd*3.),hash1(rnd*4.));
}

vec3 erot(vec3 p, vec3 ax, float ro) {
  return mix(dot(p,ax)*ax,p,cos(ro)) + sin(ro)*cross(ax,p);
}

float comp (vec3 p , vec3 off, vec3 ro) {
  float ph = acos(ro.x);
  float th = ro.y*3.1415;
  vec3 ax = vec3(cos(ph),sin(ph)*cos(th), sin(ph)*sin(th));
  p+=off;
  p = erot(p, ax, ro.z*3.1415);
  p = fract(p)-0.5;
  return length(p)-pow(1./(3.1415*2.),1./3.);
}

float scene(vec3 p) {
  float dt = 0.;
  float cc = 0.;
  for (int i = 0; i < 5; i++ ) {
    vec3 off = hash3(float(i));
    vec3 ro = hash3(cos(float(i)));
    dt += comp(p, off, ro);
    cc+=1.;
  }
  return dt/sqrt(cc*2.);
}

float linedist(vec2 p, vec2 a, vec2 b) {
  float k = dot(p-a,b-a)/dot(b-a,b-a);
  return distance(p, mix(a,b,clamp(k,0.,1.)));
}

float bpm = 22.*6.;

float hockey(float y) {
 return 0.5*(y+sqrt(4.+y*y)); 
}

float pentafriend(vec2 uv) {
  float dt = 10000.;
  for (int i = 0; i < 5; i++ ) {
    float ag = 3.1415/5.*2.*float(i) + floor(time/60.*bpm)*3.1415/10.;
    float off = 3.1415/5.*2.;
    vec2 a = vec2(sin(ag),cos(ag));
    vec2 b = vec2(sin(ag+off),cos(ag+off));
    dt = min(dt, linedist(uv*3., a, b));
  }
  return smoothstep(0.08,0.09,dt);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
  vec2 uv = vec2(fragCoord.x / iResolution.x, fragCoord.y / iResolution.y);
  uv -= 0.5;
  uv /= vec2(iResolution.y / iResolution.x, 1);
  
  float dt = pentafriend(uv);

  vec3 cam = normalize(vec3(0.4, uv));
  float sift = time/4.;
  float srt = 2.;
  vec3 init = vec3(-4.+time,cos(sift)*srt,sin(sift)*srt);
  vec3 p = init;
  float k = 1.;
  for (int i =0;i< 15; i++) {
    float dist = scene(p);
    if(i == 0) k = sign(dist);
    dist*=k;
    //dist = hockey(dist)*2;
    //if (abs(dist)<0.001) break;
    if (distance(p, init) > 100.) break;
    p += cam*dist;
  }
  float c = length(sin(p)*0.5+0.5)/sqrt(3.);
  float d = exp(-distance(p,init))/exp(0.);
  vec3 c1 = vec3(0.3,0.1,0.9);
  vec3 c2 = vec3(0.9,0.2,0.1);
  fragColor.xyz = sqrt(c1*c1*c + c2*c2*d) + hash(uv*time)*0.05;
  
  fragColor = sqrt(mix(pow(1.-fragColor, vec4(2.)), pow(fragColor, vec4(2.)), dt));
}
'''


class StFiveSidesShouldBeEnough(GlslSource):
    name = 'st_five_sides_should_be_enough'
    label = 'Five Sides Should Be Enough'
    description = 'animated version of my freestyle graphics entry for demosplash. programmed in bonzomatic in 15 minutes while waiting for my flight from toronto. set ANIMATED...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.3)
