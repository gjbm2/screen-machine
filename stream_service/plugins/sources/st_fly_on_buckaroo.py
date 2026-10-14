"""Fly On, Buckaroo: this time more cuter :3 buckaroo is flying to his boyfriend's house music is: [url]https://soundcloud.com/h4ck4y473m32/rave-3000[/url] original:...

Source: https://www.shadertoy.com/view/3s2BWV
Author: blackle
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.

//antialising. increasing to 8 looks pretty good, but will be 8x slower
#define AA_SAMPLES 1

const int BLM[17] = int[17](
    0x19B12B,0x1AB56B,0x18B167,0x1AB56B,0x18952B,0x1FFFFF,
    0x1DAA4F,0x1DAADF,0x1DAA4F,0x1DAAEF,0x1CB64F,0x1FFFFF,
    0xA2048,0x2B6DA,0x236C9,0xAB6DA,0xAB6CA
);

float box(vec2 p, vec2 d) {
    vec2 q = abs(p)-d;
    return length(max(q,0.)) + min(0.,max(q.x,q.y));
}

vec3 erot(vec3 p, vec3 ax, float ro) {
  return mix(dot(ax,p)*ax, p, cos(ro))+sin(ro)*cross(ax,p);
}

float comp(vec3 p, vec3 ax, float ro) {
  
  p = erot(p,ax,ro);
  p = asin(sin(p));
  return length(p)-1.;
}

float cloudssdf(vec3 p) {
  p.y += iTime*.2;
  float d1 = comp(p, normalize(vec3(1,2,5)), 0.5);
  p.y += iTime*.2;
  float d3 = comp(p*2., normalize(vec3(3,1,1)), 2.5)/2.;
  p.y += iTime*.2;
  float d4 = comp(p*3., normalize(vec3(4,-2,5)), 3.5)/3.;
  return (d1+d3+d4)/3.;
}

float linedist (vec3 p, vec3 a, vec3 b) {
  float k = dot(p-a,b-a)/dot(b-a,b-a);
  return distance(p,mix(a,b,clamp(k,0.,1.)));
}

float smin(float a, float b, float k) {
    float h = max(0.,k-abs(b-a))/k;
    return min(a,b)-h*h*h*k/6.;
}

float body;
float beamm;
vec3 locl;
float rm;
float scene(vec3 p) {
  p.z += sin(iTime);
  p = erot(p, vec3(0,1,0), cos(iTime)*.2);
  beamm = 0.9*(linedist(p, vec3(0), vec3(0,0,-10))-.3-sin(p.z*3.+iTime*4.)*.05 - sin(iTime)*.2);
  locl = p;
  vec3 p2 =p;
  p2.z = sqrt(p2.z*p2.z+0.02);
  p2.z+=3.;
  vec3 p3 =p;
  p3.z = sqrt(p3.z*p3.z+0.02);
  p3.z+=8.1;
  body = length(p2)-3.8;
  body += smoothstep(0.8,.9,sin(atan(p2.x,p2.y)*10.))*.02;
  body += smoothstep(0.9,1.,sin(atan(p2.x,p2.y)*45.))*.003;
  rm=length(p3)-8.525;
  body = smin(body, rm,.01);
  float hat = length(p-vec3(0,0,0.8))-0.7;
  return min(min(body,hat), beamm);
}
float bpm = 124.;
float eye;
float buckaroo(vec3 p) {
  float bpmt = iTime/60.*bpm;
  float t = pow(sin(fract(bpmt)*3.14/2.), 20.);
  p.z += sin(iTime);
  p = erot(p, vec3(0,1,0), cos(iTime)*.2);
  p-=vec3(0,0,0.9);
  p.z += t*.1;
  p.x = abs(p.x);
  float b =  length(p)-0.2;
  float scl = cos(bpmt*3.1415)*.05+.95;
  b = smin(b, linedist(p, vec3(0), vec3(.3,0,.3)*scl)-.03, 0.05);
  b = smin(b, length(p-vec3(.3,0,.3)*scl)-.07, 0.1);
  b = smin(b, linedist(p, vec3(0), vec3(0,0,-.5))-.15, .1);
  eye = length(p-vec3(.1,.18,.0))-.03;
  return min(b, eye);
}

vec3 norm(vec3 p) {
  mat3 k = mat3(p,p,p)-mat3(0.01);
  return normalize(scene(p)-vec3(scene(k[0]), scene(k[1]), scene(k[2])));
}

vec3 norm2(vec3 p) {
  mat3 k = mat3(p,p,p)-mat3(0.01);
  return normalize(buckaroo(p)-vec3(buckaroo(k[0]), buckaroo(k[1]), buckaroo(k[2])));
}

vec3 srgb(float r, float g, float b) {
  return vec3(r*r,g*g,b*b);
}

vec3 clouds(inout vec3 p, vec3 cam, vec3 init, int depth) {
  p = init;
  for (int i = 0; i < depth; i++) {
    float dist = min(scene(p),cloudssdf(p));
    dist = sqrt(dist*dist+0.05);
    p += dist*cam;
  }
  float f1 = length(sin(p)*.5+.5)/sqrt(3.);
  float f2 = smoothstep(0., 30., distance(p,init));
  vec3 sun = max(0.,dot(vec3(1./sqrt(3.)), cam))*vec3(1);
  sun = pow(sun,vec3(9)) + pow(sun,vec3(4))*srgb(0.7,0.5,0.2);
  return mix(srgb(0.2,0.3,0.7), srgb(0.8,0.3,0.3), f1) + mix(srgb(0.2,0.4,0.7), srgb(0.7,0.7,0.7), f2) + sun;
}


vec3 pixel_color( vec2 uv )
{
  //we have the moral obligation to use every platform to speak out against injustice
  //if you're financially comfortable, donate to bail funds or charities
  //pick a number that makes you as uncomfortable as the police brutality
  vec2 uv2 = uv + vec2(cos(iTime)*0.03, sin(iTime)*.1);
  ivec2 id = ivec2(floor(vec2(55,28)-uv2*100.));
  float blm = 1.;
  if (id.x >= 0 && id.x < 21 && id.y >= 0 && id.y < 17) {
    blm = (BLM[id.y] >> id.x) % 2 == 0 ? 0. : 1.;
  }
  float bubble = sqrt(length(pow(uv2-vec2(.44,.2),vec2(2.))))-.14;
  uv2 = uv2 - vec2(0.38,0.2);
  uv2 = erot(vec3(uv2,0),vec3(0,0,1),1.3-sin(iTime)*.1).xy;
  float arrow = box((vec2(2,1)*uv2)*mat2(.71,-.71,.71,.71), vec2(.1))-.01;
  bubble = min(bubble,arrow/1.7);
    
    
  vec3 cam = normalize(vec3(1,uv));
  
  float bpmt = iTime/60.*bpm;
  float t = mix(floor(bpmt) + pow(sin(fract(bpmt)*3.14/2.), 20.), bpmt, 0.8);
  vec3 init = vec3(-8.+sin(t)*2.,0,0.1);
  cam = erot(cam, vec3(0,0,1), t*.2);
  init = erot(init, vec3(0,0,1), t*.2);
  vec3 clp;
  vec3 p = init;
  bool hit = false;
  float dist;
  float glow = 0.;
  for (int i = 0; i < 180 && !hit; i++) {
    dist = scene(p);
    if (!isnan(beamm)) glow += .5/(1.+beamm*100.);
    hit = dist*dist < 1e-6;
    p+=dist*cam;
    if(distance(p,init)>20.)break;
  }
  float stmp = smoothstep(-.05,.05,dist-rm);
  vec3 lllocl = locl;
  glow = min(glow,1.);
  bool bdy = (dist == body);
  vec3 n = norm(p);
  vec3 obj = hit ? sin(n)*.5+.5 : vec3(0);
  vec3 clds = clouds(clp, cam, init, 20);
  if (hit) {
    vec3 p2 = p+n*.1;
    float ao = smoothstep(-.1,.1,scene(p2));
    vec3 r = reflect(cam,n);
    float tex = smoothstep(.4,.6,texture(iChannel0, lllocl.xy*.5).x);
    tex=mix(tex,1.,stmp);
    float fres = 1.-abs(dot(cam,n))*.98*(tex*.25+.5);
    vec3 rflcld = clouds(p2, r, p2, 15-int(tex*5.));
    obj = rflcld*fres*ao;
    if (!bdy) {
      p2 = p+cam;
      r = refract(cam,n,1.1);
      vec3 p5 = p;
      bool hhit = false;
      float bb;
      for (int i = 0; i < 50 && !hhit; i++) {
        bb = buckaroo(p5);
        hhit = bb*bb< 1e-6;
        p5+=bb*r;
        if(distance(p5,p)>2.)break;
      }
      bool ey = eye==bb;
      if (hhit) {
        vec3 n5 = norm2(p5);
        vec3 r5 = reflect(cam,n5);
        float ss = smoothstep(-.05,.05,buckaroo(p5+.05));
        float fk = length(sin(n5*vec3(.5,.5,2)+.2)*.5+.5)/sqrt(3.)*.5 + ss*.5;
        float spec = length(sin(r5*vec3(.5,.5,2)+.2)*.5+.5)/sqrt(3.);
        obj = fk*mix(ey ? srgb(0.1,0.1,0.1) : srgb(0.3,0.75,0.3), rflcld, 0.3) + obj*.5 + pow(spec, 8.)*(ey?1.:.15);
      } else {
      	obj = obj*.5 + clouds(p2, r, p2, 20)*.9;
      }
    }
  }
  obj = obj  + srgb(0.2,0.4,0.6)*glow;
  float fctr = smoothstep(-3.,1., distance(clp,init)-distance(p,init));
  vec3 coll = mix(clds, obj, fctr) + glow*glow*.9*sqrt(fctr*.5+.5);
  coll = abs(erot(sqrt(coll), normalize(sin(clp*.3+t)), 0.15));

  blm =  mix(0.,blm,smoothstep(0.,.003,abs(bubble)-.01));
  return mix(vec3(blm),coll*coll, smoothstep(0.,.003,bubble));
}

vec2 weyl_2d(int n) {
    return fract(vec2(n*12664745, n*9560333)/exp2(24.));
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord-.5*iResolution.xy)/iResolution.y;
    fragColor = vec4(0);
    for (int i = 0; i < AA_SAMPLES+int(min(0,iFrame)); i++) {
        vec2 uv2 = uv + weyl_2d(i)/iResolution.y*1.25;
        fragColor += vec4(pixel_color(uv2), 1.);
    }
	fragColor.xyz = sqrt(fragColor.xyz/fragColor.w);
}'''


class StFlyOnBuckaroo(GlslSource):
    name = 'st_fly_on_buckaroo'
    label = 'Fly On, Buckaroo'
    description = "this time more cuter :3 buckaroo is flying to his boyfriend's house music is: [url]https://soundcloud.com/h4ck4y473m32/rave-3000[/url] original:..."
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'noise', 1: 'music'}
    params = scene_params({}, render_scale=0.3)
