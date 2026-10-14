"""Springs of Arbitrary Profile: How to make a spring whose profile is any arbitrary (symmetric) signed distance function. click to look around

Source: https://www.shadertoy.com/view/ttB3DV
Author: blackle
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.

//How to make a spring whose profile is any arbitrary signed distance function.
//This is not particularly exact, but the code can be quite small when you sizecode it
//also it requires that the profile be symmetric along the x and y axes.

//// PROFILES ////
float profileSphere(vec2 p, float r) {
    return length(p) - r;
}

float profileLine(vec2 p, vec2 dim) {
    return distance(p, vec2(clamp(p.x, -dim.x/2.0, dim.x/2.0), 0.0))-dim.y;
}

//the rest of these are from http://iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm
float profileSquare(vec2 p, vec2 dim) {
    vec2 d = abs(p)-dim;
    return length(max(d,vec2(0))) + min(max(d.x,d.y),0.0);
}

float ndot(vec2 a, vec2 b ) { return a.x*b.x - a.y*b.y; }
float profileRhombus(vec2 p, vec2 dim) {
    vec2 q = abs(p);
    float h = clamp((-2.0*ndot(q,dim)+ndot(dim,dim))/dot(dim,dim),-1.0,1.0);
    float d = length( q - 0.5*dim*vec2(1.0-h,1.0+h) );
    return d * sign( q.x*dim.y + q.y*dim.x - dim.x*dim.y );
}

float profileCross( in vec2 p, in vec2 b, float r ) 
{
    p = abs(p); p = (p.y>p.x) ? p.yx : p.xy;
    vec2  q = p - b;
    float k = max(q.y,q.x);
    vec2  w = (k>0.0) ? q : vec2(b.y-p.x,-k);
    return sign(k)*length(max(w,0.0)) + r;
}

float profileHexagon( in vec2 p, in float r )
{
    const vec3 k = vec3(-0.866025404,0.5,0.577350269);
    p = abs(p);
    p -= 2.0*min(dot(k.xy,p),0.0)*k.xy;
    p -= vec2(clamp(p.x, -k.z*r, k.z*r), r);
    return length(p)*sign(p.y);
}

//// SPRING DISTANCE FUNCTION ////
vec3 closestPointOnCylinder(vec3 p, vec2 dim) {
    return vec3(normalize(p.xy)*dim.x, clamp(p.z, -dim.y/2.0, dim.y/2.0));
}

float profileForIndex(vec2 p, int profile) {
    float dist;
    if (profile == 0) {
        dist = profileSphere(p, 0.12);
    } else if (profile == 1) {
        dist = profileLine(p, vec2(0.4, 0.04));
    } else if (profile == 2) {
        dist = profileSquare(p, vec2(0.05,0.1))-0.02;
    } else if (profile == 3) {
        dist = profileRhombus(p, vec2(0.1,0.07))-0.03;
    } else if (profile == 4) {
        dist = profileCross(p, vec2(0.15,0.05), 0.0)-0.03;
    } else {
        dist = profileHexagon(p, 0.1)-0.03;
    }
    return dist;
}

float spring(vec3 p, int profile) {
    float radius = 0.5;
    float height = 3.0 + sin(iTime);
    float coils = 5.0/(height/3.141);

    vec3 pc = closestPointOnCylinder(p, vec2(radius, height));

    float distToCyl = distance(p, pc);
	float distToCoil = asin(sin(p.z*coils + 0.5*atan(p.x,p.y)))/coils;
    
    vec2 springCoords = vec2(distToCyl, distToCoil);
    
    //the multiplication factor is here to reduce the chance of the ray jumping through the back spring
    return profileForIndex(springCoords, profile) * ( max(radius/2.0-abs(length(p.xy)-radius), 0.0)*0.3 + 0.7);
}

//// ARRAY OF SPRINGS ////
float scene(vec3 p) {
    p -= vec3(0.0,4.0,0.0);
    float dist = 10000.0;
    for (int i = 0; i < 6; i++) {
        dist = min(dist, spring(p, i));
        dist = min(dist, profileSphere(vec2(p.x, 0.1+profileForIndex((p.yz+vec2(0.0,2.8))*0.4, i)), 0.1));
        p += vec3(0.0,1.5,0.0);
    }
        
    return dist;
}

vec3 sceneGrad(vec3 p) {
    vec2 epsi = vec2(0.001,0.0);
    float d1 = scene(p);
    return normalize(vec3(
        scene(p+epsi.xyy) - d1,
        scene(p+epsi.yxy) - d1,
        scene(p+epsi.yyx) - d1
    ));
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = fragCoord/iResolution.xy*2.0 - 1.0;
    vec2 mouse = iMouse.xy/iResolution.xy*2.0-1.0;
    uv.x *= iResolution.x/iResolution.y;

    vec3 origin = vec3(16.0,0.0,0.0);
    vec3 dir = -normalize(origin + vec3(0.0,uv)*3.0);
	origin+=vec3(0.0,0.0,-0.4);
    
    mat3 rot_x = mat3( cos(mouse.x), sin(mouse.x), 0.0,
                      -sin(mouse.x), cos(mouse.x), 0.0,
                                0.0,          0.0, 1.0);
    
    mat3 rot_y = mat3( cos(mouse.y), 0.0, sin(mouse.y),
                                0.0, 1.0, 0.0,
                      -sin(mouse.y), 0.0, cos(mouse.y));
    
    if (iMouse.z > 0.0) {
	    origin*=rot_y*rot_x;
    	dir*=rot_y*rot_x;
    }
    
    vec3 point = origin;
    bool interesected = false;
    for (int i = 0; i < 80; i++) {
        float dist = scene(point);
        if (dist > 20.0) break;
        if (abs(dist) < 0.001) {
            interesected = true;
            break;
        }
        point += dir * dist;
    }

    float col = 0.0;
    if (interesected) {
        vec3 grad = sceneGrad(point);
        col = abs(dot(dir,grad));
    }

    fragColor = vec4(col);
}'''


class StSpringsOfArbitraryProfile(GlslSource):
    name = 'st_springs_of_arbitrary_profile'
    label = 'Springs of Arbitrary Profile'
    description = 'How to make a spring whose profile is any arbitrary (symmetric) signed distance function. click to look around'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
