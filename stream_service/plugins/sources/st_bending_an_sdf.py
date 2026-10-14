"""Bending an SDF: a way to bend space that (kinda) preserves distances

Source: https://www.shadertoy.com/view/3llfRl
Author: blackle
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.

float super(vec3 p) {
    return sqrt(length(p*p));
}

float box(vec3 p, vec3 d, float k) {
    vec3 q = abs(p)-d;
    vec3 e = max(vec3(0), q);
    return mix(super(e),length(e),k) + min(0., max(q.x, max(q.y, q.z)));
}

vec2 kink(vec2 p, vec2 c, float k) {
    p -= c;
    //to polar coordinates
    float ang = atan(p.x, p.y);
    float len = length(p);
    //warp angle with sigmoid function
    ang -= ang/sqrt(1.+ang*ang)*(1.-k);
    //to cartesian coordiantes
    return vec2(sin(ang),cos(ang))*len + c;
}


float linedist(vec3 p, vec3 a, vec3 b) {
    float k = dot(p-a,b-a)/dot(b-a,b-a);
    return distance(p, mix(a,b,clamp(k,0.,1.)));
}

vec3 erot(vec3 p, vec3 ax, float ro) {
    return mix(dot(ax,p)*ax, p, cos(ro))+sin(ro)*cross(ax,p);
}

//smin modified to expose the "h" variable so we can use it to texture the place where the objects meet differently
vec2 smin(float a, float b, float k) {
    float h = max(0., k-abs(b-a))/k;
    return vec2(min(a,b)-h*h*h*k/6., h);
}

vec3 local_coords;
float edges;
float scene(vec3 p) {
    p.z -= 2.;
    p.yz = kink(p.yz, vec2(0,-.4), mix(1.,.0,sin(iTime)*.5+.5));
    local_coords = p;
    
    float warp = dot(sin(erot(p,vec3(1,0,0),.5)*20.),cos(p*15.));
    warp += dot(sin(erot(p,vec3(0,1,0),.5)*40.),cos(p*35.))*.5;
    warp = smoothstep(-.5,.5,warp);
    
    float bar = linedist(p,vec3(0,4,0),vec3(0,-4,0))-.1;
    float bar2 = linedist(p,vec3(0.3,4,.15),vec3(-.2,-2,.15))-.035;
    float knob = box(p-vec3(0,4,0),vec3(.35,.9,.2), warp)-.05;
    float knob2 = box(p-vec3(.5,4,0),vec3(.35,.35,.5), warp)-.05;
    vec3 bc = abs(p-vec3(0,-3,0))-vec3(0,.8,0);
    float bob = mix(length(bc), super(bc),length(sin(bc*32.)*.5+.5)*.1)-.5;
    float sm = .05+warp*warp*.05;
    vec2 boxes = smin(knob,knob2,sm);
    vec2 obj = smin(min(bar,bar2),min(boxes.x,bob),sm);
    edges = max(boxes.y, obj.y);
    return obj.x;
}

vec3 norm(vec3 p) {
    mat3 k = mat3(p,p,p)-mat3(0.001);
    return normalize(scene(p) - vec3(scene(k[0]),scene(k[1]),scene(k[2])) );
}


vec3 fancy_mapping(vec3 p) {
    vec3 d1 = textureLod(iChannel0,p.xz*.5, 4.).xyz;
    vec3 d2 = textureLod(iChannel0,p.yz*.5, 3.).xyz;
    return textureLod(iChannel0,p.xy + d2.yz*3. + d1.xy*2.,1.).xyz;
}

float bayer(vec2 fragCoord) {
    return texelFetch(iChannel1, ivec2(fragCoord) % 8, 0).x;
}

#define AO(p,n,k) smoothstep(-k,k,scene(p+n*k))
void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord-0.5*iResolution.xy)/iResolution.y;
    vec2 mouse = (iMouse.xy - 0.5*iResolution.xy)/iResolution.y;

    vec3 cam = normalize(vec3(1.5,uv));
    vec3 init = vec3(-10,0,0);

   	float yrot = 0.1;
    float zrot = iTime*.25;
    if (iMouse.z > 0.) {
        yrot = -4.*mouse.y;
        zrot = 4.*mouse.x;
    }
    cam = erot(cam, vec3(0,1,0), yrot);
    init = erot(init, vec3(0,1,0), yrot);
    cam = erot(cam, vec3(0,0,1), zrot);
    init = erot(init, vec3(0,0,1), zrot);

    vec3 p = init;
    bool hit = false;
    for (int i = 0; i < 500 && !hit; i++) {
        float dist = scene(p);
        hit = dist*dist < 1e-6;
        p+=dist*cam;
        if (distance(p, init) > 50.) break;
    }
    vec3 lcrd = local_coords;
    float ledg = edges;
	vec3 n = norm(p);
    vec3 r = reflect(cam, n);
    vec3 tex = fancy_mapping(lcrd*.8) + fancy_mapping(lcrd*.5) + fancy_mapping(lcrd*.3);
    ledg = smoothstep(0.,.5,ledg);

    float ao = AO(p,n,.1) * AO(p,n,.3) * AO(p,n,.7);
    float specao = AO(p,r,.1) * AO(p,r,.3) * AO(p,r,.7);
    float spec = length(sin(r*vec3(3,2,2))*0.4+0.6)/sqrt(3.);
    float diff = length(sin(n*vec3(2,1,1))*0.4+0.6)/sqrt(3.);
    float specex = mix(20.+sin(tex.x*14.)*12., 3., ledg);
  
    tex = smoothstep(0.,1.,tex*.25);
    vec3 col = pow(spec, specex)*specao + mix(spec,diff,ledg*.5)*ao*tex;
    float bg = pow(length(sin(cam*vec3(3,2,2))*0.4+0.6)/sqrt(3.), 10.);
    bg = smoothstep(-0.05,1.5,bg);
    fragColor.xyz = hit ? col : vec3(bg);
    fragColor.xyz = sqrt(fragColor.xyz) + bayer(fragCoord)/128.;
}'''


class StBendingAnSdf(GlslSource):
    name = 'st_bending_an_sdf'
    label = 'Bending an SDF'
    description = 'a way to bend space that (kinda) preserves distances'
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'noise', 1: 'noise'}
    params = scene_params({}, render_scale=0.35)
