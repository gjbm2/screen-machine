"""Ghostly Cavern: interesting raymarched noise function with transparency effects

Source: https://www.shadertoy.com/view/WdVXRm
Author: blackle
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.

#define FK(k) floatBitsToInt(cos(k))^floatBitsToInt(k)
float hash1(float k) {
    int x = FK(k);int y = FK(cos(k));
    return float((x*x-y)*(y*y+x)-x)/2.14e9;
}

vec3 hash3(float k) {
    float r1 = hash1(k);
    float r2 = hash1(r1);
    float r3 = hash1(r2);
    return vec3(r1, r2, r3);
}

//rotate P around axis AX by angle RO
vec3 rotate(vec3 p, vec3 ax, float ro) {
    return mix(dot(p,ax)*ax, p, cos(ro)) + sin(ro)*cross(ax,p);
}

vec3 sphericalCoordinates(vec2 p) {
    float phi = acos(p.x);
    float theta = p.y*3.1415;
    return vec3(cos(phi), sin(phi)*sin(theta), sin(phi)*cos(theta));
}

vec3 domainRepetition(vec3 p, vec3 scale) {
    return (fract(p/scale)-0.5)*scale;
}

vec4 component(vec3 p, vec3 offset, vec3 rotation) {
    vec3 axis = sphericalCoordinates(rotation.xy);
    float angle = rotation.z * 3.1415;
    p = rotate(p, axis, angle);
    p = domainRepetition(p + offset, vec3(1));

    vec3 normal = rotate(normalize(p), axis, -angle);
    return vec4(length(p)-0.48, normal);
}

vec4 scene(vec3 p) {
    vec4 accum = vec4(0.);
    float iters = 5.;
    for (float i = 0.; i < iters; i++) {
    	vec3 off = hash3(i);
    	vec3 rot = hash3(hash1(i));
    	accum += component(p, off, rot);
    }
    return accum/sqrt(iters*1.5)-0.1;
}

float phong(vec3 norm, vec3 light) {
    return abs(dot(norm, light));
}

vec3 shade(vec3 p, vec3 norm, vec3 cam) {
    float d1 = length(sin(p)*0.5+0.5)/sqrt(3.);
    float d2 = length(sin(norm)*0.5+0.5)/sqrt(3.);
    return sqrt(phong(norm, cam)*( d1*vec3(0.8,0.2,0.1) + (1.-d2)*vec3(0.3,0.6,0.9)  ));
}

void castRay(vec3 cam, inout vec3 p, inout vec4 dist) {
    float sgn = 1.;
    for (int i = 0; i < 100; i++) {
        dist = scene(p);
        if (i == 0) sgn = sign(dist.x);
        if (abs(dist.x) < 0.001) return;
        p += cam*dist.x*sgn;
    }
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // Normalized pixel coordinates (from 0 to 1)
    vec2 uv = (fragCoord-0.5*iResolution.xy)/iResolution.y;

    fragColor = vec4(0.0);
    
    vec3 cam = normalize(vec3(0.5, uv));
    vec3 init = vec3(iTime,0.,0.);
    vec3 p = init;
    vec4 dist; vec3 norm;
    castRay(cam, p, dist);
    norm = normalize(dist.yzw);
    vec3 col1 = shade(p, norm, cam);
    
    float pdist = distance(p, init);
    float transparency = pow(1./(pdist+1.),8.);
    float fog1 = pow(exp(-pdist*0.5)/exp(0.),0.5);
    
    vec3 col2 = col1;
    if (transparency > 0.02) {
    	p+=cam*0.1;
        init = p;
    	castRay(cam, p, dist);
    	norm = normalize(dist.yzw);
    	col2 = shade(p, norm, cam);
    }

    float pdist2 = distance(p, init);
    float fog2 = pow(exp(-pdist2*0.5)/exp(0.),0.5);
    
    fragColor.xyz = mix(col1*fog1, col2*fog2, transparency);
}'''


class StGhostlyCavern(GlslSource):
    name = 'st_ghostly_cavern'
    label = 'Ghostly Cavern'
    description = 'interesting raymarched noise function with transparency effects'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
