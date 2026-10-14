"""Suture Fluid: Fake fluid dynamical system that creates viscous-fingering–like flow pattern, converted from <https://www.shadertoy.com/view/XddSRX>

Source: https://github.com/nwhetsell/isf-shaders/blob/HEAD/Suture%20Fluid.fs
Author: cornusammonis <https://www.shadertoy.com/user/cornusammonis>
License: Prosperity-3.0.0
license_class: noncommercial
Imported verbatim from GLSL (nwhetsell/isf-shaders); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Uses LYGIA (Prosperity licence, noncommercial).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Filter",
        "Generator"
    ],
    "CREDIT": "cornusammonis <https://www.shadertoy.com/user/cornusammonis>",
    "DESCRIPTION": "Fake fluid dynamical system that creates viscous-fingering–like flow pattern, converted from <https://www.shadertoy.com/view/XddSRX>",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "NAME": "inputImageAmount",
            "LABEL": "Input image amount",
            "TYPE": "float",
            "DEFAULT": 0,
            "MIN": 0,
            "MAX": 1
        },
        {
            "NAME": "restart",
            "LABEL": "Restart",
            "TYPE": "event"
        },
        {
            "NAME": "enableMouse",
            "LABEL": "Enable mouse",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "mouse",
            "TYPE": "point2D",
            "DEFAULT": [0.5, 0.5],
            "MIN": [0, 0],
            "MAX": [1, 1]
        },
        {
            "NAME": "curlScale",
            "LABEL": "Curl scale",
            "TYPE": "float",
            "DEFAULT": -0.6,
            "MIN": -1,
            "MAX": 0
        },
        {
            "NAME": "laplacianScale",
            "LABEL": "Laplacian scale",
            "TYPE": "float",
            "DEFAULT": 0.05,
            "MIN": -1,
            "MAX": 1
        },
        {
            "NAME": "laplacianDivergenceScale",
            "LABEL": "Laplacian divergence scale",
            "TYPE": "float",
            "DEFAULT": -0.8,
            "MIN": -1,
            "MAX": 1
        },
        {
            "NAME": "divergenceScale",
            "LABEL": "Divergence scale",
            "TYPE": "float",
            "DEFAULT": -0.05,
            "MIN": -1,
            "MAX": 1
        },
        {
            "NAME": "divergenceUpdateScale",
            "LABEL": "Divergence update scale",
            "TYPE": "float",
            "DEFAULT": -0.04,
            "MIN": -1,
            "MAX": 1
        },
        {
            "NAME": "divergenceSmoothing",
            "LABEL": "Divergence smoothing",
            "TYPE": "float",
            "DEFAULT": 0.3,
            "MIN": 0,
            "MAX": 1
        },
        {
            "NAME": "advectionDistanceScale",
            "LABEL": "Advection distance scale",
            "TYPE": "float",
            "DEFAULT": 6,
            "MIN": 1,
            "MAX": 10
        },
        {
            "NAME": "curlRotationAnglePower",
            "LABEL": "Curl rotation angle power",
            "TYPE": "float",
            "DEFAULT": 1,
            "MIN": 0,
            "MAX": 10
        },
        {
            "NAME": "selfAmplification",
            "LABEL": "Self-amplification",
            "TYPE": "float",
            "DEFAULT": 1,
            "MIN": 0,
            "MAX": 10
        },
        {
            "NAME": "updateSmoothing",
            "LABEL": "Update smoothing",
            "TYPE": "float",
            "DEFAULT": 0.8,
            "MIN": 0,
            "MAX": 1
        },
        {
            "NAME": "diagonalWeight",
            "LABEL": "Diagonal weight",
            "TYPE": "float",
            "DEFAULT": 0.6,
            "MIN": 0,
            "MAX": 1
        }
    ],
    "ISFVSN": "2",
    "PASSES": [
        {
            "TARGET": "fluid",
            "PERSISTENT": true,
            "FLOAT": true
        },
        {

        }
    ]
}*/
/*
contributors: [Stefan Gustavson, Ian McEwan]
description: modulus of 289
use: <float|vec2|vec3|vec4> mod289(<float|vec2|vec3|vec4> x)
*/
#define FNC_MOD289 
float mod289(const in float x) { return x - floor(x * (1. / 289.)) * 289.; }
vec2 mod289(const in vec2 x) { return x - floor(x * (1. / 289.)) * 289.; }
vec3 mod289(const in vec3 x) { return x - floor(x * (1. / 289.)) * 289.; }
vec4 mod289(const in vec4 x) { return x - floor(x * (1. / 289.)) * 289.; }
/*
contributors: [Stefan Gustavson, Ian McEwan]
description: permute
use: <float|vec2|vec3|vec4> permute(<float|vec2|vec3|vec4> x)
examples:
    - https://raw.githubusercontent.com/patriciogonzalezvivo/lygia_examples/main/math_functions.frag
*/
#define FNC_PERMUTE 
float permute(const in float v) { return mod289(((v * 34.0) + 1.0) * v); }
vec2 permute(const in vec2 v) { return mod289(((v * 34.0) + 1.0) * v); }
vec3 permute(const in vec3 v) { return mod289(((v * 34.0) + 1.0) * v); }
vec4 permute(const in vec4 v) { return mod289(((v * 34.0) + 1.0) * v); }
/*
contributors: [Stefan Gustavson, Ian McEwan]
description: Fast, accurate inverse square root. 
use: <float|vec2|vec3|vec4> taylorInvSqrt(<float|vec2|vec3|vec4> x)
*/
#define FNC_TAYLORINVSQRT 
float taylorInvSqrt(in float r) { return 1.79284291400159 - 0.85373472095314 * r; }
vec2 taylorInvSqrt(in vec2 r) { return 1.79284291400159 - 0.85373472095314 * r; }
vec3 taylorInvSqrt(in vec3 r) { return 1.79284291400159 - 0.85373472095314 * r; }
vec4 taylorInvSqrt(in vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }
/*
contributors: [Stefan Gustavson, Ian McEwan]
description: grad4, used for snoise(vec4 v)
use: grad4(<float> j, <vec4> ip)
*/
#define FNC_GRAD4 
vec4 grad4(float j, vec4 ip) {
    const vec4 ones = vec4(1.0, 1.0, 1.0, -1.0);
    vec4 p,s;
    p.xyz = floor( fract (vec3(j) * ip.xyz) * 7.0) * ip.z - 1.0;
    p.w = 1.5 - dot(abs(p.xyz), ones.xyz);
    s = vec4(lessThan(p, vec4(0.0)));
    p.xyz = p.xyz + (s.xyz*2.0 - 1.0) * s.www;
    return p;
}

/*
contributors: [Stefan Gustavson, Ian McEwan]
description: Simplex Noise https://github.com/stegu/webgl-noise
use: snoise(<vec2|vec3|vec4> pos)
license: |
    Copyright 2021-2023 by Stefan Gustavson and Ian McEwan.
    Published under the terms of the MIT license:
    https://opensource.org/license/mit/
examples:
    - /shaders/generative_snoise.frag
*/
#define FNC_SNOISE 
float snoise(in vec2 v) {
    const vec4 C = vec4(0.211324865405187, // (3.0-sqrt(3.0))/6.0
                        0.366025403784439, // 0.5*(sqrt(3.0)-1.0)
                        -0.577350269189626, // -1.0 + 2.0 * C.x
                        0.024390243902439); // 1.0 / 41.0
    // First corner
    vec2 i = floor(v + dot(v, C.yy) );
    vec2 x0 = v - i + dot(i, C.xx);
    // Other corners
    vec2 i1;
    //i1.x = step( x0.y, x0.x ); // x0.x > x0.y ? 1.0 : 0.0
    //i1.y = 1.0 - i1.x;
    i1 = (x0.x > x0.y) ? vec2(1.0, 0.0) : vec2(0.0, 1.0);
    // x0 = x0 - 0.0 + 0.0 * C.xx ;
    // x1 = x0 - i1 + 1.0 * C.xx ;
    // x2 = x0 - 1.0 + 2.0 * C.xx ;
    vec4 x12 = x0.xyxy + C.xxzz;
    x12.xy -= i1;
    // Permutations
    i = mod289(i); // Avoid truncation effects in permutation
    vec3 p = permute( permute( i.y + vec3(0.0, i1.y, 1.0 ))
    + i.x + vec3(0.0, i1.x, 1.0 ));
    vec3 m = max(0.5 - vec3(dot(x0,x0), dot(x12.xy,x12.xy), dot(x12.zw,x12.zw)), 0.0);
    m = m*m ;
    m = m*m ;
    // Gradients: 41 points uniformly over a line, mapped onto a diamond.
    // The ring size 17*17 = 289 is close to a multiple of 41 (41*7 = 287)
    vec3 x = 2.0 * fract(p * C.www) - 1.0;
    vec3 h = abs(x) - 0.5;
    vec3 ox = floor(x + 0.5);
    vec3 a0 = x - ox;
    // Normalise gradients implicitly by scaling m
    // Approximation of: m *= inversesqrt( a0*a0 + h*h );
    m *= 1.79284291400159 - 0.85373472095314 * ( a0*a0 + h*h );
    // Compute final noise value at P
    vec3 g;
    g.x = a0.x * x0.x + h.x * x0.y;
    g.yz = a0.yz * x12.xz + h.yz * x12.yw;
    return 130.0 * dot(m, g);
}
float snoise(in vec3 v) {
    const vec2 C = vec2(1.0/6.0, 1.0/3.0) ;
    const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
    // First corner
    vec3 i = floor(v + dot(v, C.yyy) );
    vec3 x0 = v - i + dot(i, C.xxx) ;
    // Other corners
    vec3 g = step(x0.yzx, x0.xyz);
    vec3 l = 1.0 - g;
    vec3 i1 = min( g.xyz, l.zxy );
    vec3 i2 = max( g.xyz, l.zxy );
    //   x0 = x0 - 0.0 + 0.0 * C.xxx;
    //   x1 = x0 - i1  + 1.0 * C.xxx;
    //   x2 = x0 - i2  + 2.0 * C.xxx;
    //   x3 = x0 - 1.0 + 3.0 * C.xxx;
    vec3 x1 = x0 - i1 + C.xxx;
    vec3 x2 = x0 - i2 + C.yyy; // 2.0*C.x = 1/3 = C.y
    vec3 x3 = x0 - D.yyy; // -1.0+3.0*C.x = -0.5 = -D.y
    // Permutations
    i = mod289(i);
    vec4 p = permute( permute( permute(
                i.z + vec4(0.0, i1.z, i2.z, 1.0 ))
            + i.y + vec4(0.0, i1.y, i2.y, 1.0 ))
            + i.x + vec4(0.0, i1.x, i2.x, 1.0 ));
    // Gradients: 7x7 points over a square, mapped onto an octahedron.
    // The ring size 17*17 = 289 is close to a multiple of 49 (49*6 = 294)
    float n_ = 0.142857142857; // 1.0/7.0
    vec3 ns = n_ * D.wyz - D.xzx;
    vec4 j = p - 49.0 * floor(p * ns.z * ns.z); //  mod(p,7*7)
    vec4 x_ = floor(j * ns.z);
    vec4 y_ = floor(j - 7.0 * x_ ); // mod(j,N)
    vec4 x = x_ *ns.x + ns.yyyy;
    vec4 y = y_ *ns.x + ns.yyyy;
    vec4 h = 1.0 - abs(x) - abs(y);
    vec4 b0 = vec4( x.xy, y.xy );
    vec4 b1 = vec4( x.zw, y.zw );
    //vec4 s0 = vec4(lessThan(b0,0.0))*2.0 - 1.0;
    //vec4 s1 = vec4(lessThan(b1,0.0))*2.0 - 1.0;
    vec4 s0 = floor(b0)*2.0 + 1.0;
    vec4 s1 = floor(b1)*2.0 + 1.0;
    vec4 sh = -step(h, vec4(0.0));
    vec4 a0 = b0.xzyw + s0.xzyw*sh.xxyy ;
    vec4 a1 = b1.xzyw + s1.xzyw*sh.zzww ;
    vec3 p0 = vec3(a0.xy,h.x);
    vec3 p1 = vec3(a0.zw,h.y);
    vec3 p2 = vec3(a1.xy,h.z);
    vec3 p3 = vec3(a1.zw,h.w);
    //Normalise gradients
    vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2, p2), dot(p3,p3)));
    p0 *= norm.x;
    p1 *= norm.y;
    p2 *= norm.z;
    p3 *= norm.w;
    // Mix final noise value
    vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
    m = m * m;
    return 42.0 * dot( m*m, vec4( dot(p0,x0), dot(p1,x1),
                                dot(p2,x2), dot(p3,x3) ) );
}
float snoise(in vec4 v) {
    const vec4 C = vec4( 0.138196601125011, // (5 - sqrt(5))/20  G4
                        0.276393202250021, // 2 * G4
                        0.414589803375032, // 3 * G4
                        -0.447213595499958); // -1 + 4 * G4
    // First corner
    vec4 i = floor(v + dot(v, vec4(.309016994374947451)) ); // (sqrt(5) - 1)/4
    vec4 x0 = v - i + dot(i, C.xxxx);
    // Other corners
    // Rank sorting originally contributed by Bill Licea-Kane, AMD (formerly ATI)
    vec4 i0;
    vec3 isX = step( x0.yzw, x0.xxx );
    vec3 isYZ = step( x0.zww, x0.yyz );
    //  i0.x = dot( isX, vec3( 1.0 ) );
    i0.x = isX.x + isX.y + isX.z;
    i0.yzw = 1.0 - isX;
    //  i0.y += dot( isYZ.xy, vec2( 1.0 ) );
    i0.y += isYZ.x + isYZ.y;
    i0.zw += 1.0 - isYZ.xy;
    i0.z += isYZ.z;
    i0.w += 1.0 - isYZ.z;
    // i0 now contains the unique values 0,1,2,3 in each channel
    vec4 i3 = clamp( i0, 0.0, 1.0 );
    vec4 i2 = clamp( i0-1.0, 0.0, 1.0 );
    vec4 i1 = clamp( i0-2.0, 0.0, 1.0 );
    //  x0 = x0 - 0.0 + 0.0 * C.xxxx
    //  x1 = x0 - i1  + 1.0 * C.xxxx
    //  x2 = x0 - i2  + 2.0 * C.xxxx
    //  x3 = x0 - i3  + 3.0 * C.xxxx
    //  x4 = x0 - 1.0 + 4.0 * C.xxxx
    vec4 x1 = x0 - i1 + C.xxxx;
    vec4 x2 = x0 - i2 + C.yyyy;
    vec4 x3 = x0 - i3 + C.zzzz;
    vec4 x4 = x0 + C.wwww;
    // Permutations
    i = mod289(i);
    float j0 = permute( permute( permute( permute(i.w) + i.z) + i.y) + i.x);
    vec4 j1 = permute( permute( permute( permute (
                i.w + vec4(i1.w, i2.w, i3.w, 1.0 ))
            + i.z + vec4(i1.z, i2.z, i3.z, 1.0 ))
            + i.y + vec4(i1.y, i2.y, i3.y, 1.0 ))
            + i.x + vec4(i1.x, i2.x, i3.x, 1.0 ));
    // Gradients: 7x7x6 points over a cube, mapped onto a 4-cross polytope
    // 7*7*6 = 294, which is close to the ring size 17*17 = 289.
    vec4 ip = vec4(1.0/294.0, 1.0/49.0, 1.0/7.0, 0.0) ;
    vec4 p0 = grad4(j0, ip);
    vec4 p1 = grad4(j1.x, ip);
    vec4 p2 = grad4(j1.y, ip);
    vec4 p3 = grad4(j1.z, ip);
    vec4 p4 = grad4(j1.w, ip);
    // Normalise gradients
    vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2, p2), dot(p3,p3)));
    p0 *= norm.x;
    p1 *= norm.y;
    p2 *= norm.z;
    p3 *= norm.w;
    p4 *= taylorInvSqrt(dot(p4,p4));
    // Mix contributions from the five corners
    vec3 m0 = max(0.6 - vec3(dot(x0,x0), dot(x1,x1), dot(x2,x2)), 0.0);
    vec2 m1 = max(0.6 - vec2(dot(x3,x3), dot(x4,x4) ), 0.0);
    m0 = m0 * m0;
    m1 = m1 * m1;
    return 49.0 * ( dot(m0*m0, vec3( dot( p0, x0 ), dot( p1, x1 ), dot( p2, x2 )))
                + dot(m1*m1, vec2( dot( p3, x3 ), dot( p4, x4 ) ) ) ) ;
}
vec2 snoise2( vec2 x ){
    float s = snoise(vec2( x ));
    float s1 = snoise(vec2( x.y - 19.1, x.x + 47.2 ));
    return vec2( s , s1 );
}
vec3 snoise3( vec3 x ){
    float s = snoise(vec3( x ));
    float s1 = snoise(vec3( x.y - 19.1 , x.z + 33.4 , x.x + 47.2 ));
    float s2 = snoise(vec3( x.z + 74.2 , x.x - 124.5 , x.y + 99.4 ));
    return vec3( s , s1 , s2 );
}
vec3 snoise3( vec4 x ){
    float s = snoise(vec4( x ));
    float s1 = snoise(vec4( x.y - 19.1 , x.z + 33.4 , x.x + 47.2, x.w ));
    float s2 = snoise(vec4( x.z + 74.2 , x.x - 124.5 , x.y + 99.4, x.w ));
    return vec3( s , s1 , s2 );
}
/*
contributors: Patricio Gonzalez Vivo
description: returns a 2x2 rotation matrix
use: <mat2> rotate2d(<float> radians)
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_ROTATE2D 
mat2 rotate2d(const in float r){
    float c = cos(r);
    float s = sin(r);
    return mat2(c, s, -s, c);
}
//
// Shadertoy Buffer A
//
struct FluidComponents {
    vec3 center;
    vec3 north;
    vec3 east;
    vec3 south;
    vec3 west;
    vec3 northwest;
    vec3 southwest;
    vec3 northeast;
    vec3 southeast;
};
FluidComponents FluidComponents_create(vec2 center, vec2 stepSizes)
{
    float step_x = stepSizes.x;
    float step_y = stepSizes.y;
    FluidComponents components;
    components.center = IMG_NORM_PIXEL(fluid, fract(center)).xyz;
    components.north = IMG_NORM_PIXEL(fluid, fract(center + vec2( 0, step_y))).xyz;
    components.east = IMG_NORM_PIXEL(fluid, fract(center + vec2( step_x, 0))).xyz;
    components.south = IMG_NORM_PIXEL(fluid, fract(center + vec2( 0, -step_y))).xyz;
    components.west = IMG_NORM_PIXEL(fluid, fract(center + vec2(-step_x, 0))).xyz;
    components.northwest = IMG_NORM_PIXEL(fluid, fract(center + vec2(-step_x, step_y))).xyz;
    components.southwest = IMG_NORM_PIXEL(fluid, fract(center + vec2(-step_x, -step_y))).xyz;
    components.northeast = IMG_NORM_PIXEL(fluid, fract(center + vec2( step_x, step_y))).xyz;
    components.southeast = IMG_NORM_PIXEL(fluid, fract(center + vec2( step_x, -step_y))).xyz;
    return components;
}
vec3 FluidComponents_laplacian(FluidComponents components, float centerWeight, float edgeWeight, float vertexWeight)
{
    return centerWeight * components.center +
           edgeWeight * (components.north + components.east + components.west + components.south) +
           vertexWeight * (components.northwest + components.southwest + components.northeast + components.southeast);
}
void main()
{
    vec2 position = gl_FragCoord.xy;
    vec2 texelSize = 1. / RENDERSIZE;
    vec2 normalizedPosition = position * texelSize;
    if (PASSINDEX == 0) // Shadertoy Buffer A
    {
        // It’s unclear whether dividing by the advection distance scale is what
        // was intended in the original Shadertoy shader.
        float laplacianCenterWeight = -20. / advectionDistanceScale;
        float laplacianEdgeWeight = 4. / advectionDistanceScale;
        float laplacianVertexWeight = 1. / advectionDistanceScale;
        FluidComponents components = FluidComponents_create(normalizedPosition, texelSize);
        // .x and .y are the x and y components, .z is divergence
        // laplacian of all components
        vec3 laplacian = FluidComponents_laplacian(components, laplacianCenterWeight, laplacianEdgeWeight, laplacianVertexWeight);
        float scaledLaplacianDivergence = laplacianDivergenceScale * laplacian.z;
        vec2 normalizedCenterComponent = components.center.xy == vec2(0) ? components.center.xy : normalize(components.center.xy);
        // calculate divergence
        // vectors point inwards towards the center point
        float divergence = components.south.y - components.north.y - components.east.x + components.west.x +
                           diagonalWeight * (components.northwest.x - components.northwest.y -
                                             components.northeast.x - components.northeast.y +
                                             components.southwest.x + components.southwest.y +
                                             components.southeast.y - components.southeast.x);
        float smoothedDivergence = components.center.z + divergenceUpdateScale * divergence + divergenceSmoothing * laplacian.z;
        // reverse advection
        vec3 advectionLaplacian = FluidComponents_laplacian(
            FluidComponents_create(normalizedPosition - components.center.xy * advectionDistanceScale * texelSize, texelSize),
            0.25, 0.125, 0.0625
        );
        // temp values for the update rule
        vec2 ab = laplacianScale * laplacian.xy +
                  scaledLaplacianDivergence * normalizedCenterComponent.xy +
                  divergenceScale * smoothedDivergence * components.center.xy +
                  selfAmplification * advectionLaplacian.xy;
        // calculate curl
        // vectors point clockwise about the center point
        float curl = components.north.x - components.south.x - components.east.y + components.west.y +
                     diagonalWeight * (components.northwest.x + components.northwest.y +
                                       components.northeast.x - components.northeast.y +
                                       components.southwest.y - components.southwest.x -
                                       components.southeast.y - components.southeast.x);
        // rotate
        ab = rotate2d(curlScale * sign(curl) * pow(abs(curl), curlRotationAnglePower)) * ab;
        vec3 abd = updateSmoothing * components.center + (1. - updateSmoothing) * vec3(ab, smoothedDivergence);
        if (enableMouse) {
            vec2 displacement = position - mouse * RENDERSIZE;
            float distance = length(displacement);
            if (distance > 0.) {
                abd.xy += exp(-0.1 * distance) * normalize(displacement);
            }
        }
        // initialize with noise
        if (FRAMEINDEX < 1 || restart) {
            vec2 scaledPosition = 16. * normalizedPosition;
            gl_FragColor.rgb = vec3(snoise(scaledPosition + 1.1), snoise(scaledPosition + 2.2), snoise(scaledPosition + 3.3));
            gl_FragColor.a = 1.;
        } else {
            gl_FragColor.rg = clamp(length(abd.xy) > 1. ? normalize(abd.xy) : abd.xy, -1., 1.);
            gl_FragColor.b = clamp(abd.z, -1., 1.);
            gl_FragColor.a = 1.;
            gl_FragColor = mix(gl_FragColor, IMG_PIXEL(inputImage, gl_FragCoord.xy), inputImageAmount);
        }
    }
    else // Shadertoy Image
    {
        vec3 abd = normalize(IMG_NORM_PIXEL(fluid, normalizedPosition).xyz);
        vec3 color = 0.5 + 0.6 * cross(abd, vec3(0.5, -0.4, 0.5));
        vec3 divergence = vec3(0.1 * abd.z);
        gl_FragColor = vec4(color + divergence, 1);
    }
}
'''


class IsfSutureFluid(GlslEffect):
    name = 'isf_suture_fluid'
    label = 'Suture Fluid'
    description = 'Fake fluid dynamical system that creates viscous-fingering–like flow pattern, converted from <https://www.shadertoy.com/view/XddSRX>'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'stylise'
    scale = 0.35
    events = ('restart',)
    params = effect_params({
        "input_image_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Input image amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "inputImageAmount"
            }
        },
        "enable_mouse": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Enable mouse"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "enableMouse"
            }
        },
        "mouse_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Mouse x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mouse",
                "c": 0
            }
        },
        "mouse_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Mouse y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mouse",
                "c": 1
            }
        },
        "curl_scale": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 0.0,
            "default": -0.6,
            "x-ui": {
                "label": "Curl scale",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "curlScale"
            }
        },
        "laplacian_scale": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": 0.05,
            "x-ui": {
                "label": "Laplacian scale",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "laplacianScale"
            }
        },
        "laplacian_divergence_scale": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": -0.8,
            "x-ui": {
                "label": "Laplacian divergence scale",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "laplacianDivergenceScale"
            }
        },
        "divergence_scale": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": -0.05,
            "x-ui": {
                "label": "Divergence scale",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "divergenceScale"
            }
        },
        "divergence_update_scale": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": -0.04,
            "x-ui": {
                "label": "Divergence update scale",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "divergenceUpdateScale"
            }
        },
        "divergence_smoothing": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.3,
            "x-ui": {
                "label": "Divergence smoothing",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "divergenceSmoothing"
            }
        },
        "advection_distance_scale": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 10.0,
            "default": 6.0,
            "x-ui": {
                "label": "Advection distance scale",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "advectionDistanceScale"
            }
        },
        "curl_rotation_angle_power": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.0,
            "x-ui": {
                "label": "Curl rotation angle power",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "curlRotationAnglePower"
            }
        },
        "self_amplification": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.0,
            "x-ui": {
                "label": "Self-amplification",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "selfAmplification"
            }
        },
        "update_smoothing": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.8,
            "x-ui": {
                "label": "Update smoothing",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "updateSmoothing"
            }
        },
        "diagonal_weight": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.6,
            "x-ui": {
                "label": "Diagonal weight",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "diagonalWeight"
            }
        }
    })
