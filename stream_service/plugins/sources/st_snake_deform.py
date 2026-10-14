"""Snake deform: Study around the snake() function, to build uv-space shaped like a snake, where each section is half a circle.

Source: https://www.shadertoy.com/view/Wt2GRh
Author: eliemichel
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Released under the MIT licence
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Released under the MIT licence
// Copyright (c) 2019 - Alt144 (Élie Michel)
// Study around the snake() function, to build uv-space shaped like a snake,
// where each section is half a circle.

#define PI 3.141593

float pbeat(float t, float p)
{
    return pow(1. - fract(t), p);
}

mat2 rot(float t)
{
    float s = sin(t);
    float c = cos(t);
    return mat2(c, s, -s, c);
}

float fill(float d)
{
    return smoothstep(.01, .0, d);
}

float sat(float x) { return clamp(x, 0., 1.); }

float triangle(vec2 uv, float radius)
{
    return min(
        fill(abs(uv.y) + (uv.x - radius) * tan(PI/6.)),
    	fill(-uv.x - radius)
    );
}

float pattern(vec2 uv)
{
    vec2 udx = floor(uv*5.);
    uv.y += mod(udx.x, 2.)/5. * 0.5;
    udx = floor(uv*5.);
    vec2 guv = fract(uv*5.)-.5;
    guv = rot(0.) * guv;
    return triangle(guv, mix(0.25, 0.45, pbeat(iTime + udx.x*.01, 10.0)));
}

float lstep(float a, float b, float x)
{
    return (x - a) / (b - a);
}

vec2 ring(vec2 uv, float innerRadius, float outerRadius)
{
    float a = atan(uv.y, uv.x);
    float r = length(uv);
    uv = vec2(a / PI, sat(smoothstep(innerRadius, outerRadius, r)));
    return uv;
}

/**
 * uv: us-space to deform
 * rad1: Radius of the top arcs
 * rad2: Radius of the bottom arcs
 * th: Thickness of the snake
 */
vec2 snake(vec2 uv, float rad1, float rad2, float th)
{
    float radsum = rad1 + th + rad2;
    vec2 uv0 = uv;
    vec2 uv2 = uv;
    
    uv.x = mod(uv.x - radsum, 2. * radsum) - radsum;
    uv2.x = mod(uv2.x, 2. * radsum) - radsum;
    
    uv = ring(uv, rad1, rad1 + th);
    uv2 = ring(uv2 * vec2(-1.,1.), rad2, rad2 + th);
    float mid = (rad2+th/2.)/radsum;
    uv2.x = mix(0.0, mid, lstep(-1.0, 0.0, uv2.x));
    uv.x = mix(mid, 1.0, lstep(0.0, 1.0, uv.x));
    uv2.y = 1. - uv2.y;
    vec2 uv3 = mix(uv, uv2, step(0., -uv0.y));

    uv = mix(uv, uv2, step(0., -uv0.y));
    return uv;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord.xy * 2. - iResolution.xy)/iResolution.x;
    vec2 uv0 = uv;
    float rad = 0.3;
    float th = 0.1;
    
    uv = snake(uv, rad - th, rad, th);
    uv.x *= 4./5.;
    
    float ss = 0.05;
    float t = mix(0., 1., fract(iTime * .5));
    float test = step(t,uv.x) * step(uv.x, t+ss);
    test *= step(.1, uv.y) * step(uv.y, 0.9);
    
    vec2 tuv = uv - vec2(iTime*0.1,0.);
    vec3 col = vec3(0., pattern(tuv), test);
    
    col = vec3(1.,1.,.9);
    col = mix(col, vec3(.95, 0.15, 0.1), fill((abs(fract(tuv.x*5.-.05)-0.5)-0.15)*2.0));
    col = mix(col, vec3(.9, 0.7, 0.2), fill((abs(fract(tuv.x*5.+.05)-0.5)-0.15)*2.0));
    col = mix(col, vec3(.05, 0.35, 0.9), fill((abs(fract(tuv.x*5.)-0.5)-0.15)*2.0));
    
    col = mix(col, vec3(.1), pattern(tuv * vec2(15.,1.)) * step(.001, tuv.y) * step(tuv.y, .999));
    
    float o = 0.07;
    uv = snake(uv0, rad-th - o, rad + o, th);
    col = mix(col, vec3(.1), fill(max(.45 - uv.y, uv.y - .55) * 0.35));
    
    o = -o;
    uv = snake(uv0, rad-th - o, rad + o, th);
    col = mix(col, vec3(.1), fill(max(.45 - uv.y, uv.y - .55) * 0.35));
    
    o -= 0.01;
    uv = snake(uv0, rad-th - o, rad + o, th);
    //col = mix(col, vec3(.1), fill((.55 - uv.y) * 0.35));
    
    fragColor = vec4(col, 1.0);
}
'''


class StSnakeDeform(GlslSource):
    name = 'st_snake_deform'
    label = 'Snake deform'
    description = 'Study around the snake() function, to build uv-space shaped like a snake, where each section is half a circle.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
