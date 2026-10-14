"""Weird Machine That Does Nothing: made in like an hour a half on discord live with some friends. it has some texture swimming problems but I didn't want to deal with it >.<

Source: https://www.shadertoy.com/view/WslfD7
Author: blackle
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.

//set FOCALBLUR to 1 and SAMPS to 3 for a prettier shader, but beware, it's slow!
#define FOCALBLUR 0
#define SAMPS 1

float super(vec3 p) {
    return sqrt(length(p*p));
}
float super(vec2 p) {
    return sqrt(length(p*p));
}

float box(vec3 p, vec3 d) {
    vec3 q = abs(p)-d;
    return super(max(vec3(0),q)) + min(0., max(q.x,max(q.y,q.z)));
}

float fancyrings(vec3 p) {
    p=abs(p);
    p.xy = vec2(max(p.x,p.y),min(p.y,p.x));
    float ring = length(vec2(super(p.xy)-1.8,p.z))-0.2;
    float metaring = super(vec2(length(p.xz-vec2(1.8,0.))-0.5, p.y-(0.5+0.25*cos(iTime*8.))))-0.2;
    return min(ring,metaring);
}

vec3 erot(vec3 p, vec3 ax, float ro) {
    return mix(dot(p,ax)*ax, p, cos(ro))+sin(ro)*cross(ax,p);
}

float scene(vec3 p) {
    float b = box(erot(p,vec3(0,0,1),p.z*0.5+iTime),vec3(0.25,0.25,4))-0.5;
    float scale = 0.65;
    p.z = (asin(sin(p.z*scale*3.14)*0.99)/3.14)/scale;
    float ring1 = fancyrings(p+vec3(0,0,.75));
    float ring2 = fancyrings(erot(p,vec3(0,0,1),radians(45.))-vec3(0,0,0.75));
    return min(min(ring1,ring2),b);
}

#define FK(k) floatBitsToInt(cos(k))^floatBitsToInt(k)
float hash(float a, float b) {
    int x = FK(a);int y = FK(b);
    return float((x*x-y)*(y*y+x)-x)/2.14e9;
}

float noise(vec2 p) {
    vec2 id = floor(p);
    vec2 crds = fract(p);
    float h1 = hash(id.x,id.y);
    float h2 = hash(id.x+1.,id.y);
    float h3 = hash(id.x,id.y+1.);
    float h4 = hash(id.x+1.,id.y+1.);
    return mix(mix(h1,h2,crds.x),mix(h3,h4,crds.x),crds.y);
}

float triplanar(vec3 p, vec3 n) {
    return mix(noise(p.xy), mix(noise(p.xz), noise(p.yz), n.x*n.x), 1.-n.z*n.z);
}

vec3 norm(vec3 p) {
    mat3 k = mat3(p,p,p) - mat3(0.01);
    return normalize(scene(p) - vec3(scene(k[0]),scene(k[1]),scene(k[2])));
}

vec3 srgb(float r, float g, float b) {
    return vec3(r*r,g*g,b*b);
}
vec3 srgb(float r) {
    return vec3(r*r);
}

float speed(float x) {
    return pow(sin(fract(x)*3.14/2.),200.)+floor(x)+x*2.;
}

vec3 pixel(vec2 uv) {
    vec3 cam = normalize(vec3(1,uv));
    vec3 init = vec3(-6,0,0);
    
    float h1 = hash(hash(uv.x,uv.y),iTime*8.);
    float h2 = hash(h1,iTime);
    float h3 = hash(h2,iTime);
    vec3 blur = normalize(tan(vec3(h1,h2,h3)))*float(FOCALBLUR);
    cam+=blur*0.025;
    init-=blur*0.1;
    
    float ramptime = speed(iTime*0.25);
    float yrot = 0.2;
    float zrot = ramptime;
    init.x += sin(ramptime*0.5);
    cam = erot(cam,vec3(0,1,0),yrot);
    init = erot(init,vec3(0,1,0),yrot);
    cam = erot(cam,vec3(0,0,1),zrot);
    init = erot(init,vec3(0,0,1),zrot);
    init.z += cos(ramptime);
    
    
    vec3 p = init;
    bool hit = false;
    for (int i = 0; i < 100 && !hit; i++) {
        float dist = scene(p);
        hit = dist*dist < 1e-6;
        p+=dist*cam;
        if (distance(p,init) > 10.) break;
    }
    vec3 n = norm(p);
    vec3 r = reflect(cam,n);
    float marble = smoothstep(-0.5,0.4,triplanar(p*4., n));
    float tex = max(triplanar(p*80., n),0.)*marble;
    float spexex = mix(8., 6., tex);
    float ao = smoothstep(-1.,1.,scene(p+r*0.3)/0.3/dot(r,n))*0.5+0.5;
    float diff1 = ao*pow(length(sin(r*2.)*0.5+0.5)/sqrt(3.),2.);
    float diff2 = ao*pow(length(sin(r*2.+3.5)*0.5+0.5)/sqrt(3.),2.);
    float diff3 = ao*pow(length(sin(r*2.5+1.)*0.5+0.5)/sqrt(3.),2.);
    vec3 col1 = mix(srgb(0.,.05,.1), srgb(0.4,.3,1.), diff1) + pow(diff1, spexex)*1.5;
    vec3 col2 = mix(srgb(0.1,.0,0.5), srgb(1.,.2,0.5), diff2) + pow(diff2, spexex)*1.5;
    vec3 col3 = mix(srgb(0.1), srgb(0.9,0.9,1.), diff3) + pow(diff3, spexex)*1.5;
    vec3 col = mat3(col2,col1,col3)*transpose(mat3(col2,col1,col3))*vec3(mix(0.3,0.1,marble));
    return hit ? col : mix(srgb(0.25), srgb(0.1), length(uv));
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord-0.5*iResolution.xy)/iResolution.y;

    vec4 col;
    for (int i = 0; i<SAMPS;i++) {
        for (int j = 0; j<SAMPS;j++) {
            vec2 off= vec2(i,j)/iResolution.y/float(SAMPS);
            col += vec4(pixel(uv+off), 1);
        }
    }
    fragColor.xyz = col.xyz/col.w;
    fragColor.xyz = sqrt(fragColor.xyz) + hash(hash(uv.x,uv.y),iTime)*0.02;
}'''


class StWeirdMachineThatDoesNothing(GlslSource):
    name = 'st_weird_machine_that_does_nothing'
    label = 'Weird Machine That Does Nothing'
    description = "made in like an hour a half on discord live with some friends. it has some texture swimming problems but I didn't want to deal with it >.<"
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
