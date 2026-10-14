"""Clouds from Above: Attempting to mimic clouds seen on satellite images of earth. My plan is to combine this with a simple sphere tracer and some procedural terrain color to...

Source: https://www.shadertoy.com/view/3lcyRs
Author: tomolt
License: public-domain
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Written by Thomas Oltmann, placed into the public domain.
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Written by Thomas Oltmann, placed into the public domain.

#define TAU 6.283185307179586476925286766559 // tau is two times pi
#define RR2 0.707106781186547524400844362105 // reciprocal of square root of 2

float rand(vec2 co) {
    return fract(sin(dot(co.xy ,vec2(12.9898,78.233))) * 43758.5453);
}

float evolve(float v) {
    v = v*100.0+iTime*1.5;
    v = sin(v/9.9)*sin(v/7.0);
    v = v*0.5+0.5;
    return v;
}

vec2 swirlgen(vec2 co) {
    float r1 = rand(co);
    float r2 = rand(co+vec2(12.0,21.0));
    r1 = evolve(r1);
    r2 = evolve(r2);
    float angle = r1*TAU;
    float len = r2;
    return vec2(sin(angle),cos(angle))*len;
}

vec2 swirloct(vec2 co) {
    vec2 i = floor(co);
    vec2 f = co-i;
    vec2 t = smoothstep(0.0,1.0,f);
    vec2 ll = swirlgen(i+vec2(0.0,0.0));
    vec2 lh = swirlgen(i+vec2(0.0,1.0));
    vec2 hl = swirlgen(i+vec2(1.0,0.0));
    vec2 hh = swirlgen(i+vec2(1.0,1.0));
    return mix(
        mix(ll, hl, t.x),
        mix(lh, hh, t.x),
        t.y);
}

vec2 swirl(vec2 co) {
    vec2 val = vec2(0.0);
    float freq = 1.0;
    float ampl = 1.0;
    for (int i = 0; i < 8; ++i) {
        val += ampl*swirloct(co*freq);
        freq *= 2.0;
        ampl *= 0.5;
    }
    return val;
}

float noisegen(vec2 co) {
    float val = rand(co);
    //val = evolve(val);
    return val;
}

vec3 noiseoct(vec2 co) {
    vec2 i = floor(co);
    vec2 f = co-i;
    vec2 t = -2.0*f*f*f+3.0*f*f;
    float ll = noisegen(i+vec2(0.0,0.0));
    float lh = noisegen(i+vec2(0.0,1.0));
    float hl = noisegen(i+vec2(1.0,0.0));
    float hh = noisegen(i+vec2(1.0,1.0));
    vec3 val;
    val.z = mix(
        mix(ll, hl, t.x),
        mix(lh, hh, t.x),
        t.y);
    val.x = (mix(hl, hh, t.y) - mix(ll, lh, t.y)) * (-6.0*f.x*f.x+6.0*f.x);
    val.y = (mix(lh, hh, t.x) - mix(ll, hl, t.x)) * (-6.0*f.y*f.y+6.0*f.y);
    return val;
}

vec3 noise(vec2 co) {
    vec3 val = vec3(0.0);
    float freq = 1.0;
    float ampl = 1.0;
    for (int i = 0; i < 8; ++i) {
        val += ampl*noiseoct(co*freq);
        freq *= 1.995;
        ampl *= 0.5;
    }
    return val;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    // Normalized pixel coordinates (from 0 to 1)
    vec2 uv = fragCoord/iResolution.xy;
    
    uv *= 7.0;
    
    uv += 0.3*swirl(uv);
    
    vec3 n = noise(uv+vec2(0.11,0.04)*iTime);
    
    vec3 norm = normalize(vec3(-n.xy, 0.3));
    
    vec3 light = normalize(vec3(1.0, 1.0, 2.0));
    float diff = max(dot(norm,light),0.0);
    
    vec3 col;
    col = mix(vec3(0.0,0.7,0.8), vec3(0.9,0.9,0.8), diff);
    col = mix(vec3(0.05,0.15,0.2), col, max(n.z*1.5-1.6,0.0));
    //col = vec3(n.x*0.5+0.5, n.y*0.5+0.5,n.z);
    
    // Output to screen
    fragColor = vec4(col,1.0);
}'''


class StCloudsFromAbove(GlslSource):
    name = 'st_clouds_from_above'
    label = 'Clouds from Above'
    description = 'Attempting to mimic clouds seen on satellite images of earth. My plan is to combine this with a simple sphere tracer and some procedural terrain color to...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
