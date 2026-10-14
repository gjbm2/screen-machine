"""Packed Spheres SDF: a rhombic dodecahedral honeycomb domain repetition!

Source: https://www.shadertoy.com/view/3djBDh
Author: blackle
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.

vec3 erot(vec3 p, vec3 ax, float ro) {
    return mix(dot(p,ax)*ax,p,cos(ro))+sin(ro)*cross(ax,p);
}

//the following functions assume that p is inside the cube of radius 1 centered at the origin
//closest vertex of the cube to p
vec3 vertex(vec3 p) {
    return max(sign(p),vec3(0))*2.-1.;
}
//closest face of the cube to p
vec3 face(vec3 p) {
    vec3 ap = abs(p);
    if (ap.x>=max(ap.z,ap.y)) return vec3(sign(p.x),0.,0.);
    if (ap.y>=max(ap.z,ap.x)) return vec3(0.,sign(p.y),0.);
    if (ap.z>=max(ap.x,ap.y)) return vec3(0.,0.,sign(p.z));
    return vec3(0);
}
//closest edge of the cube to p
vec3 edge(vec3 p) {
    vec3 mask = vec3(1)-abs(face(p));
    vec3 v = vertex(p);
    vec3 a = v*mask.zxy, b = v*mask.yzx;
    return distance(p,a)<distance(p,b)?a:b;
}

float hills(vec3 p) {
    return sin(2.*dot(sin(p.xy/16.), cos(p.xy/4.)))*3.;
}

float super(vec3 p) {
    return sqrt(length(p*p));
}

//rhombic dodecahedron SDF with rounded corners
float rho_dod(vec3 p)
{
    float offset = 0.1;
    float radius = .9;
    p = sqrt(p*p+offset*offset/2.);
    p = (p+p.yzx)-radius;
    return super(max(p,0.))+min(0.,max(p.x,max(p.y,p.z)))-offset;
}

float spheres(vec3 p, out vec3 id, out vec3 loc, float density) {
    vec3 op = p;
    id = floor(p)+.5;
    vec3 d = face(p-id);
    vec3 m = sign(mod(id,2.)-1.);
    if (m.x*m.y*m.z<0.) id += d;
    if (id.z + hills(id) > -5.) { //if this ball is absent, get the distance to its neighbour
        vec3 e = edge(p-id);
        id += e;
    }
    p -= id;
    float rad = 0.7;
    float sph = mix(rho_dod(p), length(p)-.7, smoothstep(-.2,.2,cos(iTime)));
    loc = p;
    return max((op.z+3.5+hills(op))/2.,sph);
}

float scene(vec3 p) {
    vec3 id,loc;
    float s1 = spheres(p, id, loc, 0.4);
    return s1;
}

vec3 norm(vec3 p) {
    mat3 k = mat3(p,p,p)-mat3(0.001);
    return normalize(scene(p) - vec3(scene(k[0]),scene(k[1]),scene(k[2])));
}

vec3 skycol(vec3 p) {
    float dt = dot(p,normalize(vec3(1)));
    float sun = pow(max(dt,0.),10.);
    return vec3(sun) + .2+dt*.1;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord-0.5*iResolution.xy)/iResolution.y;
    vec2 mouse = (iMouse.xy-0.5*iResolution.xy)/iResolution.y;

    vec3 cam = normalize(vec3(1,uv));
    vec3 samplepos = erot(vec3(-10,0,0), vec3(0,0,1), iTime*.2);
    vec3 init = vec3(-20.,0,0);
    
    float yrot = 0.6+sin(iTime/5.)*.4;
    float zrot = iTime/4.;
    if (iMouse.z > 0.) {
        yrot = clamp(1.-4.*mouse.y,-0.,3.14/2.);
        zrot = 4.*mouse.x;
    }
    cam = erot(cam, vec3(0,1,0), yrot);
    init = erot(init, vec3(0,1,0), yrot);
    cam = erot(cam, vec3(0,0,1), zrot);
    init = erot(init, vec3(0,0,1), zrot);
    
    vec3 p = init;
    bool hit = false;
    float dist;
    for (int i = 0; i < 250 && !hit; i++) {
        dist = scene(p);
        hit = dist*dist < 1e-6;
        p+=dist*cam;
        if (distance(p,init)>50.) break;
    }
    float fog = smoothstep(5.,50.,distance(p,init));
    vec3 n = norm(p);
    vec3 r = reflect(cam,n);
    float ao = smoothstep(-6.5,-4.8,p.z + hills(p))*smoothstep(-.3,.2,scene(p+n*.2)); //fake "ao" based on sphere's depth in the stack
    float diff = length(sin(n*2.)*0.5+0.5)/sqrt(3.);
    float spec = length(sin(r*vec3(1.,2.,2.5))*0.5+0.5)/sqrt(3.);
    float frens = 1.-pow(dot(cam,n),2.)*0.98;

    
    vec3 col = vec3(.1)*diff*ao + pow(spec,10.)*frens*2.*ao;
    vec3 bg = skycol(cam);
    fragColor.xyz = (hit ? mix(col,bg,fog) : bg);
    fragColor.xyz = smoothstep(vec3(-0.1),vec3(1),sqrt(fragColor.xyz))*smoothstep(2.,.5,length(uv));
}'''


class StPackedSpheresSdf(GlslSource):
    name = 'st_packed_spheres_sdf'
    label = 'Packed Spheres SDF'
    description = 'a rhombic dodecahedral honeycomb domain repetition!'
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'noise'}
    params = scene_params({}, render_scale=0.25)
