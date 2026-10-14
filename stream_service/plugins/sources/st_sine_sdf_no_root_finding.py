"""Sine SDF (No Root Finding): finally, maybe, cracked the sine SDF problem, in the most ad-hoc way possible

Source: https://www.shadertoy.com/view/3lSyDG
Author: blackle
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.

//this is a method of computing the distance to a sine function
//it works by creating a rough estimate for the closest point,
//then checking if there's a closer point by creating a line
//segment that is tangent to the curve, and finding the closest
//point on that segment, becoming the new closest point
//
//because this works on the idea of finding the closest point,
//we can also return that point from the function alongside
//the distance. unfortunately, the point is a little jittery
//especially when far from the curve. oh well...
//
//other solutions:
//  iq:      https://www.shadertoy.com/view/3t23WG
//  fabrice: https://www.shadertoy.com/view/tsXXRM

#define PI 3.141592653

vec2 closest_on_line(vec2 p, vec2 a, vec2 b) {
    float k = dot(p-a,b-a)/dot(b-a,b-a);
    return mix(a,b,clamp(k,0.,1.));
}

vec3 sine_SDF(vec2 p, float freq) {
    float wavelen = PI/freq;

    //map p to be within the principal half cycle
    float cell = round(p.x/wavelen)*wavelen;
    float sgn = sign(cos(p.x*freq));
    p.x = (p.x-cell)*sgn;

	//sloppy initial estimate to the closest point using a line
    //inexplicably, setting the height of the line to be proportional
    //to the point's y position gives a *really* good estimate when 
    //far away from the curve.
    float correction = max(1.,abs(p.y));
    vec2 estimateline = vec2(wavelen/2.,correction);
    vec2 cp = closest_on_line(p, -estimateline, estimateline);

    //any less than 2 iterations looks bad, any more does not improve it
    for(int i = 0; i < 2; i++) {
        //refine the closest point by checking if there's a closer point on the tangent line
    	cp.y = sin(cp.x*freq);
        float derivative = freq*cos(cp.x*freq);
        vec2 localline = vec2(1,derivative)/freq/correction*.25;
    	cp = closest_on_line(p, cp-localline, cp+localline);
        cp.x = clamp(cp.x, -wavelen/2., wavelen/2.);
    }

    cp.y = sin(cp.x*freq);
    float dist = length(p-cp)*sign(p.y-sin(p.x*freq));

    //map the closest point back to global coordinates
    cp.x *= sgn; cp.x += cell;
    return vec3(dist, cp);
}

vec3 shadeDistance(float d) {
    d *= .5;
    float dist = d*120.;
    float banding = max(sin(dist), 0.0);
    float strength = sqrt(1.-exp(-abs(d)*2.));
    float pattern = mix(strength, banding, (0.6-abs(strength-0.5))*0.3);
    
    vec3 color = vec3(pattern);
    
    color *= d > 0.0 ? vec3(1.0,0.56,0.4) : vec3(0.4,0.9,1.0);

    return color;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord-0.5*iResolution.xy)/iResolution.y;
    vec2 mouse = (iMouse.xy-0.5*iResolution.xy)/iResolution.y;
	float scale = 5.;
    uv*=scale; mouse*=scale;
    float pixel_size = scale/iResolution.y;
    
    float t = sin(iTime)*.5+.5;
    float freq = mix(20.,.5,sqrt(t));

    vec3 mousedist = sine_SDF(mouse, freq);
    vec3 col = shadeDistance(sine_SDF(uv, freq).x);
    if (iMouse.z > 0.) {
        col *= smoothstep(-pixel_size,pixel_size, distance(mouse, uv) - abs(mousedist.x)) *.5 +.5;
        col = mix(vec3(.8,.9,.4), col, smoothstep(-pixel_size,pixel_size, distance(mousedist.yz, uv) - .05));
    }

    fragColor = vec4(col,1.0);
}'''


class StSineSdfNoRootFinding(GlslSource):
    name = 'st_sine_sdf_no_root_finding'
    label = 'Sine SDF (No Root Finding)'
    description = 'finally, maybe, cracked the sine SDF problem, in the most ad-hoc way possible'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
