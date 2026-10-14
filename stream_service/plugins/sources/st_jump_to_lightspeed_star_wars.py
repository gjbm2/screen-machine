"""Jump to lightspeed (Star Wars): The version from dmmn is quite cartoony, and the one from sickbow is slow and only black and white. This is my attempt to make a fast version closer to the...

Source: https://www.shadertoy.com/view/Wl2GRz
Author: nyri0
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: * You can use it under the terms of the MIT license
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/* Created by Louis Sugy, 2019
 * You can use it under the terms of the MIT license
 * (free to use even in commercial projects, attribution required)
 */

// Change the anti-aliasing to 1 if it's too slow
#define AA 2
#define PI 3.14159265
#define ATAN5 1.37340076695

vec2 cart2polar(vec2 cart) {
    return vec2(atan(cart.y, cart.x), length(cart));
}

// From https://www.shadertoy.com/view/4sc3z2
// and https://www.shadertoy.com/view/XsX3zB
#define MOD3 vec3(.1031,.11369,.13787)
vec3 hash33(vec3 p3)
{
	p3 = fract(p3 * MOD3);
    p3 += dot(p3, p3.yxz+19.19);
    return -1.0 + 2.0 * fract(vec3((p3.x + p3.y)*p3.z, (p3.x+p3.z)*p3.y, (p3.y+p3.z)*p3.x));
}
float simplexNoise(vec3 p)
{
    const float K1 = 0.333333333;
    const float K2 = 0.166666667;
    
    vec3 i = floor(p + (p.x + p.y + p.z) * K1);
    vec3 d0 = p - (i - (i.x + i.y + i.z) * K2);
    
    vec3 e = step(vec3(0.0), d0 - d0.yzx);
	vec3 i1 = e * (1.0 - e.zxy);
	vec3 i2 = 1.0 - e.zxy * (1.0 - e);
    
    vec3 d1 = d0 - (i1 - 1.0 * K2);
    vec3 d2 = d0 - (i2 - 2.0 * K2);
    vec3 d3 = d0 - (1.0 - 3.0 * K2);
    
    vec4 h = max(0.6 - vec4(dot(d0, d0), dot(d1, d1), dot(d2, d2), dot(d3, d3)), 0.0);
    vec4 n = h * h * h * h * vec4(dot(d0, hash33(i)), dot(d1, hash33(i + i1)), dot(d2, hash33(i + i2)), dot(d3, hash33(i + 1.0)));
    
    return dot(vec4(31.316), n);
}


float linearstep(float low, float high, float val)
{
    return clamp((val-low) / (high-low), 0.0, 1.0);
}
float jumpstep(float low, float high, float val)
{
    if(2.0 * val < high + low) {
        return clamp(atan(10.0 * (val-low) / (high-low) - 5.0) / (2.0 * ATAN5) + 0.5, 0.0, 1.0);
    }
    else {
        return (10.0 * (val-low) / (high-low) - 5.0) / (2.0 * ATAN5) + 0.5;
    }
}

vec3 pixelVal(vec2 coord)
{    
    // Pixel to point (the center of the screen is (0,0)
    vec3 resolution = iResolution * float(AA);
    vec2 uv = (2.0*coord - resolution.xy) / resolution.x;
    vec2 ad = cart2polar(uv);
    
    // Loop forever
    float time = mod(iTime, 8.0);
    
    vec3 bg = vec3(0,0,0.05);
    vec3 col = bg;
 
    // Initial step, just roaming in space
    if(time < 3.5) {
        float r = ad.y / (1.0+0.042*time);
    	float noiseVal = simplexNoise(vec3(60.0*ad.x,50.0*r, 0.0));
        float whiteIntensity = smoothstep(0.7, 1.0, noiseVal)
                               * (1.0 - smoothstep(2.8,3.5,time));
        col = mix(col, vec3(1,1,1), whiteIntensity);
    }
    // The effect starts
    if(time >= 2.8 && time < 4.5) {
        float blueTime = 1.0 - 1.0*smoothstep(2.8, 5.0, time);
        float whiteTime = 1.0 - 0.6*smoothstep(3.0, 5.0, time);
        float r = ad.y / (1.0+0.15*linearstep(0.0, 3.5, time))
                  *(40.0 / (3.0 + 20.0*jumpstep(2.8, 6.0, 0.5*pow(time,1.5))));
        float noiseVal = simplexNoise(vec3(40.0*ad.x,r, 0.0));
        col = mix(col, vec3(0.5,0.7,1), smoothstep(2.8, 3.5, time)
                                        *smoothstep(0.4*blueTime, blueTime, noiseVal));
        col = mix(col, vec3(1,1,1), smoothstep(2.8, 3.5, time)
                                    *smoothstep(0.7 * whiteTime, whiteTime, noiseVal));
        
        // Dark at the center
    	col = mix(col, bg, smoothstep(2.8, 3.4, time) * (1.0 - ad.y));
    }
    // Fade to white
    if(time > 4.2 && time < 4.5) {
        col = mix(col, vec3(0.9,0.95,1.0), smoothstep(4.2,4.5, time));
    }
    // Fade to black with stars (decelerating)
    else if(time > 4.5) {
        float r = ad.y / (1.0 + 2.0/PI*atan(-0.042*(8.0-time))/cos((8.0-time)/2.2));
    	float noiseVal = simplexNoise(vec3(60.0*ad.x,50.0*r, 0.0));
        float whiteIntensity = smoothstep(0.7, 1.0, noiseVal);
        col = mix(col, vec3(1,1,1), whiteIntensity);
        col = mix(col, vec3(0.9,0.95,1.0), 1.0-smoothstep(4.5,5.0, time));
    }

    return col;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec3 avgcol = vec3(0.0);
    
    for(int i = 0; i < AA; i++)
        for(int j = 0; j < AA; j++)
            avgcol += pixelVal(float(AA)*fragCoord + vec2(i, j));
        
    avgcol /= float(AA*AA);
    
    
    // Output to screen
    fragColor = vec4(avgcol, 1.0);
}'''


class StJumpToLightspeedStarWars(GlslSource):
    name = 'st_jump_to_lightspeed_star_wars'
    label = 'Jump to lightspeed (Star Wars)'
    description = 'The version from dmmn is quite cartoony, and the one from sickbow is slow and only black and white. This is my attempt to make a fast version closer to the...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
