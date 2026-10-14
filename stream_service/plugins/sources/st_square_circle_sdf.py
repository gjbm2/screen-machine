"""Square + Circle SDF: an exact SDF that generalizes the circle and the square. it can also give the closest point on the surface!

Source: https://www.shadertoy.com/view/3slBDl
Author: blackle
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.

vec2 nonzero_sign(vec2 p) {
    return max(sign(p),vec2(0))*2.-1.;
}

mat2 sort(vec2 p) {
    if (p.x > p.y) return mat2(0,1,1,0);
    return mat2(1);
}

const float PI = acos(-1.);
vec3 rounded_square(vec2 p, float radius, float sharpness, float offset) {
    //first we abs and sort the vector so we can work with the symmetries of the square
    vec2 sgn = nonzero_sign(p);
    p = abs(p);
    mat2 perm = sort(p);
    p *= perm;
    p.y += sharpness;

    //get closest point on defining sphere for axis
    float rad = sharpness + radius;
    vec2 closest = normalize(p)*rad;

    //normalize the closest point so it lies within the axis
    if (closest.x > closest.y-sharpness) {
        float k = sharpness;
        float y = closest.y;
        closest.y = (k + sqrt(2.*rad*rad-k*k))/2.;
        closest.x = closest.y - k;
    }

    //calculate the distance
    float side = sign(length(p)-rad);
    float dist = length(p-closest)*side;

    //map the closest point back to global coordinates
    closest += normalize(p-closest)*offset*side;
    if (side == 0.) closest = p;
    vec2 closest_global = (closest-vec2(0,sharpness))*transpose(perm)*sgn;
    return vec3(dist-offset, closest_global);
}

vec3 shadeDistance(float d) {
    float dist = d*150.0;
    float banding = max(sin(dist), 0.0);
    float strength = sqrt(1.-exp(-abs(d)*2.));
    float pattern = mix(strength, banding, (0.6-abs(strength-0.5))*0.3);
    
    vec3 color = vec3(pattern);
    
    color *= d > 0.0 ? vec3(1.0,0.56,0.4) : vec3(0.4,0.9,1.0);

    return color;
}

float antialias(float x) {
    float pixelsize = 3./iResolution.y;
    return smoothstep(-pixelsize, pixelsize, x);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord-0.5*iResolution.xy)/iResolution.y;
    vec2 mouse = (iMouse.xy-0.5*iResolution.xy)/iResolution.y;
    uv*=3.; mouse*=3.;
    
    float radius = sin(iTime)*0.25+0.75;
    float sharpness = pow(asin(sin(iTime*2.5))/PI+1.,8.);
    float offset = sin(iTime*0.65)*0.25+0.25;
    
    float dist = rounded_square(uv, radius, sharpness, offset).x;
    fragColor = vec4(shadeDistance(dist), 1.);

    float mousedist = rounded_square(mouse, radius, sharpness, offset).x;
    vec2 mousepoint = rounded_square(mouse, radius, sharpness, offset).yz;
    if (iMouse.z > 0.) {
        float pixelsize = 3./iResolution.y;
        fragColor *= antialias(distance(mouse, uv) - abs(mousedist))*0.5+0.5;
        fragColor = mix(vec4(1,0,0,0), fragColor, antialias(distance(mousepoint, uv) - 0.03));
    }
}'''


class StSquareCircleSdf(GlslSource):
    name = 'st_square_circle_sdf'
    label = 'Square + Circle SDF'
    description = 'an exact SDF that generalizes the circle and the square. it can also give the closest point on the surface!'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
