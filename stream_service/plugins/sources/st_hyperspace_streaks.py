"""Hyperspace Streaks: Based on Louis Sugy's shader. I've only tweaked and simplified a few bits of the effect to make it look closer to the effect we see in Solo. I'll add it to the...

Source: https://www.shadertoy.com/view/WdyXDm
Author: blue_max
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: * You can use it under the terms of the MIT license
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/* 
 * Based on https://www.shadertoy.com/view/Wl2GRz
 * Created by Louis Sugy, 2019
 * You can use it under the terms of the MIT license
 * (free to use even in commercial projects, attribution required)
 */

#define PI 3.14159265
#define ATAN5 1.37340076695

vec3 blue_col = vec3(0.5,0.7,1);

const float t2 = 2.0;

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

float jumpstep(float low, float high, float val)
{
    /*
     This curve is plotted here:

     https://www.iquilezles.org/apps/graphtoy/?f1(x)=atan(10%20*%20x%20-%205.0)%20/%20(2%20*%20atan(5.0))%20+%200.5&f2(x)=clamp(f1(x),%200,%201)&f3(x)=(10%20*%20x%20-%205.0)%20/%20(2%20*%20atan(5.0))%20+%200.5
    */
    // This part of the curve looks like a smoothstep going from 0
    // to halfway up the curve
    float f1 =  clamp(
       atan(8.0 * (val-low) / (high-low) - 5.0) / (2.0 * ATAN5) + 0.5, 
       0.0, 1.0
    );
    // This is a linear curve
    float f2 = (8.0 * (val-low) / (high-low) - 5.0) / (2.0 * ATAN5) + 0.5;
    return max(f1, f2);
}

vec3 pixelVal(vec2 coord)
{    
    // Pixel to point (the center of the screen is (0,0)
    //float t1 = 2.8, t2 = 4.5;
    vec3 resolution = iResolution * 4.0;
    vec2 uv = (2.0 * coord - resolution.xy) / resolution.x;
    vec2 ad = cart2polar(uv);
    // ad: polar coords
    // ad.x = angle
    // ad.y = radius
    
    // Loop forever
    float time = mod(iTime, t2);
    
    // Uncomment this line to revert the effect
    //time = t2 - time;
    
    //time = 0.5 * t2; // DEBUG
    float t = time / t2; // normalized [0..1] time
    
    vec3 bg = vec3(0.0);
    vec3 fg = 0.75 * vec3(0.082, 0.443, 0.7);
    vec3 col = mix(bg, fg, t);
    // whiteout:
    col = mix(col, vec3(1.0), smoothstep(0.5, 0.9, t));
 
    //time = 1.25; // DEBUG
    float intensity = 1.0;
    // Smaller r's produce longer streaks
    float r = ad.y;
    r = r * 40.0 / (5.0 + 60.0 * jumpstep(0.0, t2, 0.5*pow(time, 3.5)));

    // Lower values in the multiplier for ad.x yield thicker streaks
    float noiseVal = simplexNoise(vec3(60.0 * ad.x, r, 0.0));
    float noiseGain = 1.0 + 2.0 * smoothstep(0.5, 0.9, t);
    noiseVal *= noiseGain;
    
    // Let's remove a few streaks:
    float lo_t = clamp(mix(0.25, 0.0, t), 0.0, 1.0);
    noiseVal = smoothstep(lo_t, 1.0, noiseVal);
    
    // Overall intensity of the streaks
    intensity = mix(0.0, 10.0, t * 1.5);
    // Multiplying by ad.y darkens the center streaks a little bit
    noiseVal *= ad.y * intensity * noiseVal;
    float white_level = smoothstep(0.0, 1.0, noiseVal);
    white_level *= white_level;

    col += intensity * blue_col * vec3(noiseVal) + vec3(white_level);
    
    ///////////////////////////////////////
    // Add the white disk in the center
    ///////////////////////////////////////
    float disk_size = 0.025, falloff, disk_col;
    float disk_intensity;
    disk_intensity = smoothstep(0.25, 0.65, t);
    //disk_size = jumpstep(0.0, t2 - 0.3, 0.5*pow(time, 3.5));
        
    falloff = 3.0; // 100 = short fallof, 3.0 = bigger, nicer falloff
    // Negative fallofs will make a black disk surrounded by a halo
    disk_col = exp(-(ad.y - disk_size) * falloff);
    col += disk_intensity * disk_col * vec3(0.913, 0.964, 0.980);
    
    return col;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec3 avgcol = vec3(0.0);
    
    for(int i = -1; i <= 1; i++)
        for(int j = -1; j <= 1; j++)
            avgcol += pixelVal(4.0 * fragCoord + vec2(i, j));
    avgcol /= 9.0;

    // Output to screen
    fragColor = vec4(avgcol, 1.0);
}
'''


class StHyperspaceStreaks(GlslSource):
    name = 'st_hyperspace_streaks'
    label = 'Hyperspace Streaks'
    description = "Based on Louis Sugy's shader. I've only tweaked and simplified a few bits of the effect to make it look closer to the effect we see in Solo. I'll add it to the..."
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
