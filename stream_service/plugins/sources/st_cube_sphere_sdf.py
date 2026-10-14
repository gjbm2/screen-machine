"""Cube + Sphere SDF: generalization of a cube and a sphere, complete with closest-point calculation!

Source: https://www.shadertoy.com/view/3dlfDs
Author: blackle
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.

vec3 nonzero_sign(vec3 p) {
    return max(sign(p),vec3(0))*2.-1.;
}

mat3 sort(vec3 p) {
    mat3 perm = mat3(1);
    //there must be a more elegant way to do this...
    if (p.x > p.y) { p.xy = p.yx; perm *= mat3(0,1,0,1,0,0,0,0,1); }
    if (p.y > p.z) { p.yz = p.zy; perm *= mat3(1,0,0,0,0,1,0,1,0); }
    if (p.x > p.y) { p.xy = p.yx; perm *= mat3(0,1,0,1,0,0,0,0,1); }
    return perm;
}

vec4 rounded_square(vec3 p, float radius, float sharpness, float offset) {
    //first we abs and sort the vector so we can work with the cube symmetries
    vec3 sgn = nonzero_sign(p);
    p = abs(p);
    mat3 perm = sort(p);
    p *= perm;
    p.z += sharpness;

    //get closest point on defining sphere for this axis
    float rad = sharpness + radius;
    vec3 closest = normalize(p)*rad;

    //normalize the closest point so it lies within the axis
    if (closest.y > closest.z-sharpness) {
        float a = closest.x;
        float d = closest.y+closest.z;
        float k = sharpness;
        float z = (k + sqrt(d*d*(2.*rad*rad-k*k)/(2.*a*a+d*d)))/2.;
        float y = z - k;
        float x = a*(2.*z-k)/d;
        closest = vec3(x,y,z);
    	if (closest.x > closest.z-sharpness) {
            z = (2.*k + sqrt(3.*rad*rad - 2.*k*k))/3.;
        	y = z - k;
            x = z - k;
        	closest = vec3(x,y,z);
    	}
    }
    //calculate the distance
    float side = sign(length(p)-rad);
    float dist = length(p-closest)*side;

    //map the closest point back to global coordinates
    closest += normalize(p-closest)*offset*side;
    if (side == 0.) closest = p;
    vec3 closest_global = (closest-vec3(0,0,sharpness))*transpose(perm)*sgn;

	return vec4(dist-offset, closest_global);
}

float linedist(vec3 p, vec3 a, vec3 b) {
    float k = dot(p-a,b-a)/dot(b-a,b-a);
    return distance(p,mix(a,b,clamp(k,0.,1.)));
}

vec3 srgb(float r, float g, float b) {
    return vec3(r*r, g*g, b*b);
}
vec3 srgb(float k) {
    return vec3(k*k);
}

float PI = acos(-1.);
vec3 objcol_glob;
float scene(vec3 p) {
    float radius = sin(iTime*1.2)*0.2+0.7;
    float sharpness = pow(asin(sin(iTime*0.7))/PI+1.,8.);
    float offset = 1.-radius;
    float dist = rounded_square(p, radius, sharpness, offset).x;
	objcol_glob = srgb(0.5,0.35,0.6);
    
    vec3 probe = normalize(vec3(cos(iTime*2.), sin(iTime*2.), cos(iTime*.7)*.3+.3))*2.;
    vec3 closest = rounded_square(probe, radius, sharpness, offset).yzw;
    float closestball = length(p-closest)-0.1;
    float probeball = length(p-probe)-0.1;
    float probeline = linedist(p, probe, closest) - 0.03;
    
    if (closestball < dist) {
        objcol_glob = srgb(0.2,0.6,0.3);
        dist = closestball;
    }
    if (probeball < dist) {
        objcol_glob = srgb(0.6,0.2,0.1);
        dist = probeball;
    }
    if (probeline < dist) {
        objcol_glob = srgb(0.3);
        dist = probeline;
    }

    return dist;
}

vec3 norm(vec3 p) {
    mat3 k = mat3(p,p,p) - mat3(0.001);
    return normalize(scene(p)- vec3(scene(k[0]), scene(k[1]), scene(k[2])));
}

vec3 erot(vec3 p, vec3 ax, float ro) {
    return mix(dot(ax,p)*ax, p, cos(ro)) + sin(ro)*cross(ax,p);
}


vec3 shade(vec3 n, vec3 r, vec3 cam, float harmonic, vec3 col, vec3 rot) {
    float ang = length(rot); vec3 ax = normalize(rot);
    
    col = abs(erot(col, vec3(0,0,1), ang*0.1));
    float fresnel = 1.-abs(dot(cam,n))*0.98;
    r = erot(r, ax, -ang);
    float spec = length(sin(r*harmonic)*0.5+0.5)/sqrt(3.);
    return col*pow(spec+.2,3.) + pow(spec, 12.)*fresnel*1.6;
}

float bayer(vec2 uv) {
    return (texelFetch(iChannel0, ivec2(uv)%8, 0).x - 0.5)/128.;
}

float antialias(float x) {
    float pixelsize = 3./iResolution.y;
    return smoothstep(-pixelsize, pixelsize, x);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord-0.5*iResolution.xy)/iResolution.y;
    vec2 mouse = (iMouse.xy-0.5*iResolution.xy)/iResolution.y;
    vec3 cam = normalize(vec3(1.6, uv));
    vec3 init = vec3(-7,0,0);

   	float yrot = 0.5;
    float zrot = 2.5;
    if (iMouse.z > 0.) {
        yrot += -4.*mouse.y;
        zrot += 4.*mouse.x;
    }
    cam = erot(cam, vec3(0,1,0), yrot);
    init = erot(init, vec3(0,1,0), yrot);
    cam = erot(cam, vec3(0,0,1), zrot);
    init = erot(init, vec3(0,0,1), zrot);
    
    vec3 p = init;
    bool hit = false;
    for (int i = 0; i < 150 && !hit; i++) {
        float dist = scene(p);
        hit = dist*dist < 1e-6;
        p+=dist*cam;
    }
    vec3 objcol = objcol_glob;
    vec3 n = norm(p);
    vec3 r = reflect(cam, n);
    
    vec3 l1 = vec3(0,0,.1);
    vec3 l2 = vec3(0,1.,0);
    vec3 l3 = vec3(4.,0,0);

    vec3 col = shade(n, r, cam, 2., objcol, l1);
    vec3 bg = shade(cam, cam, vec3(0), 2., srgb(0.3), l1);
    col += shade(n, r, cam, 3.3, objcol, l2)*.6;
    bg += shade(cam, cam, vec3(0), 3.3, srgb(0.1), l2)*.6;
    col += shade(n, r, cam, 4., objcol, l3)*.3;
    bg += shade(cam, cam, vec3(0), 4., srgb(0.3), l3)*.3;
    fragColor.xyz = sqrt(hit ? col : bg*.4) + bayer(fragCoord);
}'''


class StCubeSphereSdf(GlslSource):
    name = 'st_cube_sphere_sdf'
    label = 'Cube + Sphere SDF'
    description = 'generalization of a cube and a sphere, complete with closest-point calculation!'
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'noise'}
    params = scene_params({}, render_scale=0.25)
