"""Corroding Arbitrary SDF: a technique to add pitting to an SDF in a controlled way. cycles between three different "modes". see [url]https://www.shadertoy.com/view/WsBBRw[/url] for the...

Source: https://www.shadertoy.com/view/td2fRD
Author: blackle
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.

//this shader is an experiment with mapping a 4 dimensional SDF onto a 3 dimensional one, by
//using the components of 3d space plus the distance to a given SDF. this can be used to
//map domain-repeated spheres onto the surface of an SDF, and use those spheres to cut holes
//into it, much like pitted metal. Becase these spheres are restrained to the surface of the
//SDF, we can control exactly what part of the SDF gets pitted. This shader cycles through 3
//possibilities. pitting everything, only pitting inside existing pits, or only pitting
//outside existing pits. There is no visible domain repetition pattern because I am also using
//a modified version of domain repetition where some of the domains are "disabled" such that
//they report the distance as if the neighbour domains are filled, but it is empty. this means
//you can make an arbitrary percentage of domains empty, and therefore no holes will appear

//see https://www.shadertoy.com/view/WsBBRw for the 2d case

//return the SDF for a sphere, or the SDF for an empty region surrounded by spheres
float gated_domain(vec4 p, float scale, bool gated) {
    if (!gated) {
        p.yzw = abs(p.yzw);
        if (p.y > p.z) p.yz = p.zy;
        if (p.z > p.w) p.zw = p.wz;
        if (p.y > p.z) p.yz = p.zy;
        p.w -= scale;
    }
    return length(p)-scale/2.2;
}

#define FK(k) floatBitsToInt(k)^floatBitsToInt(cos(k))
float hash(float a, float b) {
    int x = FK(a); int y = FK(b);
    return float((x*x-y)*(y*y+x)+x)/2.14e9;
}

vec3 erot(vec3 p, vec3 ax, float ro) {
    return mix(dot(ax,p)*ax,p,cos(ro))+sin(ro)*cross(ax,p);
}

float smin(float a, float b, float k) {
    float h = max(0., k-abs(a-b))/k;
    return min(a,b)-h*h*h*k/6.;
}

int pittingtype;
float scene(vec3 p) {
    float sphere = length(p)-1.;
    float cut = p.z;
    
    float top = sphere;
    float last = sphere;
    for (int i = 0; i < 5; i++) { //5 octaves of noise
        //random rotations
        p = erot(p, normalize(vec3(1,2,3)), .2);
        p = erot(p, normalize(vec3(1,3,2)), .51);

    	float scale = .5/pow(float(i+1),1.5);

        //create 4d coordinates where the first coordinate is the distance to the SDF
    	vec4 p4d = vec4(last,p);

        //domain repetition *only* along the yzw axes
    	vec3 id = floor(p4d.yzw/scale);
    	p4d.yzw = (fract(p4d.yzw/scale)-0.5)*scale;

        //disable 50% of spheres. see https://www.shadertoy.com/view/WsSBRD for another example of this technique
    	bool gated = hash(id.x, hash(id.y, id.z)) > 0.;
        float holes = gated_domain(p4d, scale, gated);
        top = -smin(-top, holes, 0.04*sqrt(scale));

        if (pittingtype == 0) last = holes; //add pitting to existing pits
        if (pittingtype == 1) last = top; //add pitting everywhere
        if (pittingtype == 2) last = sphere; //add pitting only to original surface
    }
    
    return max(top,-cut);
}

vec3 norm(vec3 p) {
    mat3 k = mat3(p,p,p)-mat3(0.001);
    return normalize(scene(p)-vec3(scene(k[0]),scene(k[1]),scene(k[2])));
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord-.5*iResolution.xy)/iResolution.y;
    vec2 mouse = (iMouse.xy-0.5*iResolution.xy)/iResolution.y;
    
    pittingtype = int(iTime)%3;

    vec3 cam = normalize(vec3(2,uv));
    vec3 init = vec3(-5,0,0);

   	float yrot = 0.5;
    float zrot = 4.5;
    if (iMouse.z > 0.) {
        yrot += -4.*mouse.y;
        zrot += 4.*mouse.x;
    } else {
        yrot += cos(iTime)*.2;
        zrot += sin(iTime)*.2;
    }
    cam = erot(cam, vec3(0,1,0), yrot);
    init = erot(init, vec3(0,1,0), yrot);
    cam = erot(cam, vec3(0,0,1), zrot);
    init = erot(init, vec3(0,0,1), zrot);
    
    vec3 p = init; 
    bool hit = false;
    //raymarch
    for (int i = 0; i < 100 && !hit; i++) {
        float dist = scene(p);
        hit = dist*dist < 1e-6;
        p+=cam*dist*.9;
        if (distance(p,init) > 10.) break;
    }
    //shading
    vec3 n = norm(p);
    vec3 r = reflect(cam, n);
    float ao = smoothstep(-.1,.1,scene(p+n*.1));
    ao *= smoothstep(-.2,.2,scene(p+n*.2));
    ao*=ao;
    float diff = length(sin(n*2.)*.5+.5)/sqrt(3.);
    float spec = length(sin(r*2.)*.5+.5)/sqrt(3.);
    float fresnel = 1.-abs(dot(cam,n))*.98;
    vec3 col = abs(erot(vec3(0.1,0.04,0.03),r,0.05))*diff*diff*ao + pow(spec, 10.)*fresnel*ao;
    fragColor.xyz = sqrt((hit ? col : vec3(0.))*2.) + abs(hash(iTime, hash(uv.x,uv.y)))*.04;
}'''


class StCorrodingArbitrarySdf(GlslSource):
    name = 'st_corroding_arbitrary_sdf'
    label = 'Corroding Arbitrary SDF'
    description = 'a technique to add pitting to an SDF in a controlled way. cycles between three different "modes". see [url]https://www.shadertoy.com/view/WsBBRw[/url] for the...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
