"""Mad Wall: ABAREKABE.

Source: https://www.shadertoy.com/view/4ssBR2
Author: c0de4
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''

// thanks to: @h_doxas https://wgld.org/
//
// rotate, twist, smoothMin, distance func, ray marching


// thanks to: Inigo Quilez http://www.iquilezles.org/
// 
// noise

// wrote: @c0de4 

vec3 hsv(float h, float s, float v){
  vec4 t = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
  vec3 p = abs(fract(vec3(h) + t.xyz) * 6.0 - vec3(t.w));
  return v * mix(vec3(t.x), clamp(p - vec3(t.x), 0.0, 1.0), s);
}

const vec3 lightDir = vec3(0.577, 0.577, 0.577);

vec3 rotate(vec3 p, float angle, vec3 axis){
    vec3 a = normalize(axis);
    float s = sin(angle);
    float c = cos(angle);
    float r = 1.0 - c;
    mat3 m = mat3(
        a.x * a.x + a.x * r + c,
        a.y * a.x * r - a.z * s,
        a.z * a.x * r - a.y * s,
        a.x * a.y * r + a.z * s,
        a.y * a.y * r + c,
        a.z * a.y * r + a.x * s,
        a.x * a.z * r + a.y * s,
        a.y * a.z * r - a.x * s,
        a.z * a.z * r + c
    );
    return m * p;
}

vec3 twistX(vec3 p, float power){
    float s = sin(power * p.y);
    float c = cos(power * p.y);
    mat3 m = mat3(
        1.0, 0.0, 0.0,
        0.0, c, s,
        0.0, -s,   c
    );
    return m * p;
}

vec3 twistY(vec3 p, float power){
    float s = sin(power * p.y);
    float c = cos(power * p.y);
    mat3 m = mat3(
          c, 0.0,  -s,
        0.0, 1.0, 0.0,
          s, 0.0,   c
    );
    return m * p;
}


float random (in vec2 st) { 
    return fract(sin(dot(st.xy,
                         vec2(12.9898,78.233))) 
                * 43758.5453123);
}

// The MIT License
// Copyright © 2013 Inigo Quilez
float noise( in vec2 p )
{
    vec2 i = floor( p );
    vec2 f = fract( p );
    
    vec2 u = f*f*(3.0-2.0*f);

    return mix( mix( random( i + vec2(0.0,0.0) ), 
                     random( i + vec2(1.0,0.0) ), u.x),
                mix( random( i + vec2(0.0,1.0) ), 
                     random( i + vec2(1.0,1.0) ), u.x), u.y);
}

// smoothing min
float smoothMin(float d1, float d2, float k){
    float h = exp(-k * d1) + exp(-k * d2 * d1);
    return -log(h) / k;
}

// torus
float torus(vec3 p, vec2 r){
    float a = -.3 * cos(iTime + p.z * p.y * p.x) * noise(vec2(p.xz+iTime));
    vec2 d = vec2(length(p.xy) + a + noise(vec2(a, iTime)) - r.x, p.z);
    return length(d * p.z) - r.x + noise(vec2(r.x+iTime*.1));
}

// sphere
float sphere( vec3 p, float r )
{
  return length(mod(p, .1))-r;
}

float box( vec3 p )
{
  return length(max(abs(p)-vec3(1.),0.0));
}

// distance function
float distFunc(vec3 p){
  float a = cos(iTime + noise(vec2(p.z, iTime))) + sin(iTime + p.z);
  float b = sin(iTime - p.x) + noise(vec2(iTime));
  
    vec3 q = rotate(twistX(twistX(p+b, 1.2 + cos(iTime)), 1.1 + sin(iTime)), radians(iTime * 100.), vec3(1.0, 0., 0.));

    float d1 = torus(p+q*.2, vec2(.6, .1));
    float d2 = sphere(p + q*-.1*cos(iTime), 1.);
    float c = smoothMin(d1, d2+noise(vec2(d1)), 16.);
    return c + sqrt(mod(c - mod(c - d2 * noise(vec2(iTime*.1)*.1), 4.) * .1, 2.) * .1);

}

vec3 genNormal(vec3 p){
    float d = 0.1;
    return normalize(vec3(
        distFunc(p + vec3(  d, 0.0, 0.0)) - distFunc(p + vec3( -d, 0.0, 0.0)),
        distFunc(p + vec3(0.0,   d, 0.0)) - distFunc(p + vec3(0.0,  -d, 0.0)),
        distFunc(p + vec3(0.0, 0.0,   d)) - distFunc(p + vec3(0.0, 0.0,  -d))
    ));
}

void mainImage( out vec4 fragColor, in vec2 fragCoord ) {
    // fragment position
    vec2 p = (gl_FragCoord.xy * 2.0 - iResolution.xy) / min(iResolution.x, iResolution.y);
    
    // camera and ray
    vec3 cPos = vec3(0., 0., -1.8);
    vec3 cDir = vec3(cos(iTime*.5)*.5 - .5, sin(iTime*.5)*.5, -1. + cos(iTime*.5)*.5);
    vec3 cUp  = vec3(0.0, 1.0, 0.0);
    vec3 cSide = cross(cDir, cUp);
    float targetDepth = 1.1;
    vec3 ray = normalize(cSide * p.x + cUp * p.y + cDir * targetDepth);
    
    // marching loop
    float tmp, dist;
    tmp = 0.0;
    vec3 dPos = cPos;
    for(int i = 0; i < 16; i++){
        dist = distFunc(dPos);
        tmp += dist;
        dPos = cPos - cos(iTime*.1)*.4 + tmp * ray * .75;
    }
    
    // hit check
    vec3 color;
    if(abs(dist) < .1){
        vec3 normal = genNormal(dPos);
        float diff = clamp(dot(lightDir, normal), 0.1, 1.0);
        color = vec3(cos(iTime)*.5, .5+sin(iTime*.2)*.2, .8) * hsv(diff*.5, .2, 1.);
    }else{
        color = vec3(0.0);
    }
    fragColor = vec4(color, 1.0);
}'''


class StMadWall(GlslSource):
    name = 'st_mad_wall'
    label = 'Mad Wall'
    description = 'ABAREKABE.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
