"""Corroding Arbitrary SDF (2 D): this is the technique in [url]https://www.shadertoy.com/view/td2fRD[/url] but a 3d SDF mapping to a 2d sdf, instead of a 4d to a 3d. should be easier to...

Source: https://www.shadertoy.com/view/WsBBRw
Author: blackle
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.

//this is the technique in https://www.shadertoy.com/view/td2fRD
//but a 3d SDF mapping to a 2d sdf, instead of a 4d to a 3d.
//should be easier to understand.

#define FK(k) floatBitsToInt(k)^floatBitsToInt(cos(k))
float hash(float a, float b) {
    int x = FK(a); int y = FK(b);
    return float((x*x-y)*(y*y+x)+x)/2.14e9;
}

//return the SDF for a sphere, or the SDF for an empty region surrounded by spheres
float gated_domain(vec3 p, float scale, bool gated) {
    if (!gated) {
        p.xy = abs(p.xy);
        if (p.x > p.y) p.xy = p.yx;
        p.y -= 1./scale;
    }
    return length(p)-.2;
}

float scene3d(vec3 p) {
    float scale = 2.;
    vec2 id = floor(p.xy*scale);
    p.xy = (fract(p.xy*scale)-0.5)/scale;
    bool gated = hash(id.x, id.y) > 0.;
    return gated_domain(p, scale, gated);
}

vec3 erot(vec3 p, vec3 ax, float ro) {
    return mix(dot(ax,p)*ax,p,cos(ro)) + sin(ro)*cross(ax,p);
}

int pittingtype;
float scene2d(vec2 p) {
    float circle = length(p)-1.;

    float top = circle;
    float last = circle;
    for (int i = 0; i < 5; i++) {
        float scale = 1./float(i+1);
        //map 3d coordinates to 4d using the distance to the SDF
    	vec3 p3d = vec3(p, last)/scale;
		//cut out mapped spheres from SDF
        float holes = scene3d(p3d)*scale;
    	top = max(top, -holes);

        if (pittingtype == 0) last = holes; //add pitting to existing pits
        if (pittingtype == 1) last = top; //add pitting everywhere
        if (pittingtype == 2) last = circle; //add pitting only to original surface
    }
    return top;
}

vec3 norm(vec3 p) {
    mat3 k = mat3(p,p,p)-mat3(0.001);
    return normalize(scene3d(p)-vec3(scene3d(k[0]),scene3d(k[1]),scene3d(k[2])));
}

vec3 render3d(vec2 uv) {
    
    vec3 cam = normalize(vec3(2,uv));
    vec3 init = vec3(-5,0,2);
    
    cam = erot(cam,vec3(0,1,0), .3);
    cam = erot(cam,vec3(0,0,1), iTime*.1);
    
    vec3 p = init; 
    bool hit = false;
    //raymarch
    for (int i = 0; i < 100 && !hit; i++) {
        float dist = scene3d(p);
        hit = dist*dist < 1e-6;
        p+=cam*dist*.9;
        if (distance(p,init) > 100.) break;
    }
    //shading
    vec3 n = norm(p);
    return hit ? sin(n)*.5+.5 : vec3(0.1);
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

vec3 render2d(vec2 uv, vec2 mouse) {
    uv *= 3.; mouse *= 3.;
    vec3 col = shadeDistance(scene2d(uv));
    
    float mousedist = scene2d(mouse);
    if (iMouse.z > 0.) {
        col *= antialias(distance(mouse, uv) - abs(mousedist))*0.5+0.5;
    }
    return col;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord-.5*iResolution.xy)/iResolution.y;
    vec2 mouse = (iMouse.xy-.5*iResolution.xy)/iResolution.y;
    pittingtype = int(iTime)%3;
    
    if (uv.x > .0) {
        fragColor.xyz = render3d(uv-vec2(0.45,0.));
    } else {
        fragColor.xyz = render2d(uv+vec2(0.45,0.), mouse+vec2(0.45,0.));
    }
}'''


class StCorrodingArbitrarySdf2D(GlslSource):
    name = 'st_corroding_arbitrary_sdf_2_d'
    label = 'Corroding Arbitrary SDF (2 D)'
    description = 'this is the technique in [url]https://www.shadertoy.com/view/td2fRD[/url] but a 3d SDF mapping to a 2d sdf, instead of a 4d to a 3d. should be easier to...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
