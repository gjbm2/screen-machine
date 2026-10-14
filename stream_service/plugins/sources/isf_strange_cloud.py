"""Strange Cloud: Fractal cloud, converted from <https://www.shadertoy.com/view/tsGSDt>

Source: https://github.com/nwhetsell/isf-shaders/blob/HEAD/Strange%20Cloud.fs
Author: loicvdb <https://github.com/loicvdb>
License: Prosperity-3.0.0
license_class: noncommercial
Imported verbatim from GLSL (nwhetsell/isf-shaders); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Uses LYGIA (Prosperity licence, noncommercial).
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Generator"
    ],
    "CREDIT": "loicvdb <https://github.com/loicvdb>",
    "DESCRIPTION": "Fractal cloud, converted from <https://www.shadertoy.com/view/tsGSDt>",
    "INPUTS": [
        {
            "NAME": "formationSpeed",
            "LABEL": "Formation speed",
            "TYPE": "float",
            "DEFAULT": 0.9,
            "MAX": 10,
            "MIN": -10
        },
        {
            "NAME": "powerAmplitude",
            "LABEL": "Power amplitude",
            "TYPE": "float",
            "DEFAULT": 5,
            "MAX": 20,
            "MIN": -20
        },
        {
            "NAME": "absorbanceFactor",
            "LABEL": "Absorbance factor",
            "TYPE": "float",
            "DEFAULT": 10,
            "MAX": 100,
            "MIN": 0
        },
        {
            "NAME": "outerRadius",
            "LABEL": "Outer radius",
            "TYPE": "float",
            "DEFAULT": 1.5,
            "MAX": 10,
            "MIN": 0
        },
        {
            "NAME": "innerRadius",
            "LABEL": "Inner radius",
            "TYPE": "float",
            "DEFAULT": 1.2,
            "MAX": 10,
            "MIN": 0
        },
        {
            "NAME": "rotationSpeed",
            "LABEL": "Rotation speed",
            "TYPE": "float",
            "DEFAULT": 0.2,
            "MAX": 10,
            "MIN": -10
        },
        {
            "NAME": "motionBlur",
            "LABEL": "Motion blur",
            "TYPE": "float",
            "DEFAULT": 0.9,
            "MAX": 1,
            "MIN": 0
        },
        {
            "NAME": "cameraFocalDistance",
            "LABEL": "Camera focal distance",
            "TYPE": "float",
            "DEFAULT": 1.6,
            "MAX": 10,
            "MIN": 0
        },
        {
            "NAME": "cameraFocalLength",
            "LABEL": "Camera focal length",
            "TYPE": "float",
            "DEFAULT": 1,
            "MAX": 10,
            "MIN": 0
        },
        {
            "NAME": "cameraAperture",
            "LABEL": "Camera aperture",
            "TYPE": "float",
            "DEFAULT": 0.075,
            "MAX": 1,
            "MIN": 0
        },
        {
            "NAME": "apertureRotation",
            "LABEL": "Aperture rotation",
            "TYPE": "float",
            "DEFAULT": 0.075,
            "MAX": 1,
            "MIN": 0
        },
        {
            "NAME": "volumeColor",
            "LABEL": "Volume color",
            "TYPE": "color",
            "DEFAULT": [0.3, 0.3, 0.3, 1]
        },
        {
            "NAME": "lightRadius",
            "LABEL": "Light distance",
            "TYPE": "float",
            "DEFAULT": 3.3166247904,
            "MIN": 0,
            "MAX": 200
        },
        {
            "NAME": "lightPhi",
            "LABEL": "Light phi (degrees)",
            "TYPE": "float",
            "DEFAULT": 72.4515993862,
            "MIN": 0,
            "MAX": 180
        },
        {
            "NAME": "lightTheta",
            "LABEL": "Light theta (degrees)",
            "TYPE": "float",
            "DEFAULT": 251.5650511771,
            "MIN": 0,
            "MAX": 360
        },
        {
            "NAME": "lightColor",
            "LABEL": "Light color",
            "TYPE": "color",
            "DEFAULT": [0.5, 0.5, 0.7, 1]
        },
        {
            "NAME": "lightIntensity",
            "LABEL": "Light intensity",
            "TYPE": "float",
            "DEFAULT": 20,
            "MAX": 100,
            "MIN": 0
        },
        {
            "NAME": "attenuationL",
            "LABEL": "Light attenuation",
            "TYPE": "float",
            "DEFAULT": 4,
            "MAX": 5,
            "MIN": 0.01
        },
        {
            "NAME": "highlightColor",
            "LABEL": "Highlight color",
            "TYPE": "color",
            "DEFAULT": [0.5, 0.1, 0.2, 1]
        },
        {
            "NAME": "enableBloom",
            "LABEL": "Enable bloom",
            "TYPE": "bool",
            "DEFAULT": true
        },
        {
            "NAME": "bloomSpread",
            "LABEL": "Bloom spread",
            "TYPE": "float",
            "DEFAULT": 2,
            "MAX": 100,
            "MIN": 0
        },
        {
            "NAME": "enableTonemap",
            "LABEL": "Enable tonemap",
            "TYPE": "bool",
            "DEFAULT": true
        },
        {
            "NAME": "positionRandomness",
            "LABEL": "Position randomness",
            "TYPE": "float",
            "DEFAULT": 0,
            "MAX": 1,
            "MIN": 0
        }
    ],
    "ISFVSN": "2",
    "PASSES": [
        {
            "TARGET": "cloud",
            "PERSISTENT": true,
            "FLOAT": true
        },
        {

        }
    ]
}*/
// The default light parameters are:
//   lightRadius = length(vec3(-1, -3, 1))
//               = sqrt(-1 * -1 + -3 * -3 + 1 * 1)
//               = sqrt(11) ≈ 3.3166247904
//   lightPhi = acos(1 / lightRadius) ≈ 72.4515993862°
//   lightTheta = atan(-3 / -1) ≈ 251.5650511771°
// #define ISF_EDITOR_WEBSITE
/*
contributors: Patricio Gonzalez Vivo
description: clamp a value between 0 and 1
use: <float|vec2|vec3|vec4> saturation(<float|vec2|vec3|vec4> value)
examples:
    - https://raw.githubusercontent.com/patriciogonzalezvivo/lygia_examples/main/math_functions.frag
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_SATURATE 
#define saturate(V) clamp(V, 0.0, 1.0)

/*
contributors: Narkowicz 2015
description: ACES Filmic Tone Mapping Curve. https://knarkowicz.wordpress.com/2016/01/06/aces-filmic-tone-mapping-curve/
use: <vec3|vec4> tonemapACES(<vec3|vec4> x)
*/
#define FNC_TONEMAPACES 
vec3 tonemapACES(vec3 v) {
    const float a = 2.51;
    const float b = 0.03;
    const float c = 2.43;
    const float d = 0.59;
    const float e = 0.14;
    return saturate((v*(a*v+b))/(v*(c*v+d)+e));
}
vec4 tonemapACES(in vec4 v) {
    return vec4(tonemapACES(v.rgb), v.a);
}
#define RANDOM_SINLESS 
#define RANDOM_HIGHER_RANGE 
/*
contributors: ["Patricio Gonzalez Vivo", "David Hoskins", "Inigo Quilez"]
description: Pass a value and get some random normalize value between 0 and 1
use: float random[2|3](<float|vec2|vec3> value)
options:
    - RANDOM_HIGHER_RANGE: for working with a range over 0 and 1
    - RANDOM_SINLESS: Use sin-less random, which tolerates bigger values before producing pattern. From https://www.shadertoy.com/view/4djSRW
    - RANDOM_SCALE: by default this scale if for number with a big range. For producing good random between 0 and 1 use bigger range
examples:
    - /shaders/generative_random.frag
license:
    - MIT License (MIT) Copyright 2014, David Hoskins
*/
#define RANDOM_SCALE vec4(.1031, .1030, .0973, .1099)
#define FNC_RANDOM 
float random(in float x) {
    x = fract(x * RANDOM_SCALE.x);
    x *= x + 33.33;
    x *= x + x;
    return fract(x);
}
float random(in vec2 st) {
    vec3 p3 = fract(vec3(st.xyx) * RANDOM_SCALE.xyz);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
float random(in vec3 pos) {
    pos = fract(pos * RANDOM_SCALE.xyz);
    pos += dot(pos, pos.zyx + 31.32);
    return fract((pos.x + pos.y) * pos.z);
}
float random(in vec4 pos) {
    pos = fract(pos * RANDOM_SCALE);
    pos += dot(pos, pos.wzxy + 33.33);
    return fract((pos.x + pos.y) * (pos.z + pos.w));
}
vec2 random2(float p) {
    vec3 p3 = fract(vec3(p) * RANDOM_SCALE.xyz);
    p3 += dot(p3, p3.yzx + 19.19);
    return fract((p3.xx + p3.yz) * p3.zy);
}
vec2 random2(vec2 p) {
    vec3 p3 = fract(p.xyx * RANDOM_SCALE.xyz);
    p3 += dot(p3, p3.yzx + 19.19);
    return fract((p3.xx + p3.yz) * p3.zy);
}
vec2 random2(vec3 p3) {
    p3 = fract(p3 * RANDOM_SCALE.xyz);
    p3 += dot(p3, p3.yzx + 19.19);
    return fract((p3.xx + p3.yz) * p3.zy);
}
vec3 random3(float p) {
    vec3 p3 = fract(vec3(p) * RANDOM_SCALE.xyz);
    p3 += dot(p3, p3.yzx + 19.19);
    return fract((p3.xxy + p3.yzz) * p3.zyx);
}
vec3 random3(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * RANDOM_SCALE.xyz);
    p3 += dot(p3, p3.yxz + 19.19);
    return fract((p3.xxy + p3.yzz) * p3.zyx);
}
vec3 random3(vec3 p) {
    p = fract(p * RANDOM_SCALE.xyz);
    p += dot(p, p.yxz + 19.19);
    return fract((p.xxy + p.yzz) * p.zyx);
}
vec4 random4(float p) {
    vec4 p4 = fract(p * RANDOM_SCALE);
    p4 += dot(p4, p4.wzxy + 19.19);
    return fract((p4.xxyz + p4.yzzw) * p4.zywx);
}
vec4 random4(vec2 p) {
    vec4 p4 = fract(p.xyxy * RANDOM_SCALE);
    p4 += dot(p4, p4.wzxy + 19.19);
    return fract((p4.xxyz + p4.yzzw) * p4.zywx);
}
vec4 random4(vec3 p) {
    vec4 p4 = fract(p.xyzx * RANDOM_SCALE);
    p4 += dot(p4, p4.wzxy + 19.19);
    return fract((p4.xxyz + p4.yzzw) * p4.zywx);
}
vec4 random4(vec4 p4) {
    p4 = fract(p4 * RANDOM_SCALE);
    p4 += dot(p4, p4.wzxy + 19.19);
    return fract((p4.xxyz + p4.yzzw) * p4.zywx);
}

// The raymarching volume renderer used in this shader seems to be heavily
// customized, so that it’s not really possible to adapt it to LYGIA’s
// raymarcher. For consistency’s sake, we can still use macros with the same
// names as LYGIA.
#define RAYMARCH_MIN_DIST max(length(rayOrigin) - outerRadius, 0.)
#define RAYMARCH_VOLUME_SAMPLES 150
#define RAYMARCH_MAX_DIST (0.03 * float(RAYMARCH_VOLUME_SAMPLES))
#define RAYMARCH_VOLUME_SAMPLES_LIGHT 7
#define RAYMARCH_VOLUME_DITHER 1.0
#define LIGHT_DIRECTION polar2cart(lightRadius, lightPhi * DEG2RAD, lightTheta * DEG2RAD)
/*
contributors: Shadi El Hajj
description: Medium Structure
license: MIT License (MIT) Copyright (c) 2024 Shadi EL Hajj
*/
#define STR_MEDIUM 
struct Medium {
    vec3 scattering;
    vec3 absorption;
    float sdf;
};

/*
contributors: Shadi El Hajj
description: |
    Medium Constructor.
use:
    - void mediumNew(out <medium> _mat)
    - <medium> mediumNew()
license: MIT License (MIT) Copyright (c) 2024 Shadi EL Hajj
*/
#define FNC_MEDIUM_NEW 
void mediumNew(out Medium _mat) {
    _mat.scattering = vec3(1.0, 1.0, 1.0);
    _mat.absorption = vec3(1.0, 1.0, 1.0);
    _mat.sdf = RAYMARCH_MAX_DIST;
}
Medium mediumNew() {
    Medium mat;
    mediumNew(mat);
    return mat;
}
Medium mediumNew(vec3 scattering, vec3 absorption, float sdf) {
    Medium mat = mediumNew();
    mat.scattering = scattering;
    mat.absorption = absorption;
    mat.sdf = sdf;
    return mat;
}
Medium mediumNew(vec3 scattering, float sdf) {
    return mediumNew(scattering, vec3(0.0, 0.0, 0.0), sdf);
}

/*
contributors: Patricio Gonzalez Vivo
description: some useful math constants
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define EIGHTH_PI 0.39269908169
#define QTR_PI 0.78539816339
#define HALF_PI 1.5707963267948966192313216916398
#define PI 3.1415926535897932384626433832795
#define TWO_PI 6.2831853071795864769252867665590
#define TAU 6.2831853071795864769252867665590
#define INV_PI 0.31830988618379067153776752674503
#define INV_SQRT_TAU 0.39894228040143267793994605993439
#define SQRT_HALF_PI 1.25331413732
#define PHI 1.618033988749894848204586834
#define EPSILON 0.0000001
#define GOLDEN_RATIO 1.6180339887
#define GOLDEN_RATIO_CONJUGATE 0.61803398875
#define GOLDEN_ANGLE 2.39996323
#define DEG2RAD (PI / 180.0)
#define RAD2DEG (180.0 / PI)
/*
contributors: Patricio Gonzalez Vivo
description: extend GLSL Max function to add more arguments
use:
    - <float> mmax(<float> A, <float> B, <float> C[, <float> D])
    - <vec2|vec3|vec4> mmax(<vec2|vec3|vec4> A)
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_MMAX 
float mmax(in float a, in float b) { return max(a, b); }
float mmax(in float a, in float b, in float c) { return max(a, max(b, c)); }
float mmax(in float a, in float b, in float c, in float d) { return max(max(a, b), max(c, d)); }
float mmax(const vec2 v) { return max(v.x, v.y); }
float mmax(const vec3 v) { return mmax(v.x, v.y, v.z); }
float mmax(const vec4 v) { return mmax(v.x, v.y, v.z, v.w); }
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
/*
contributors: Patricio Gonzalez Vivo
description: returns a 3x3 rotation matrix
use: <mat3> rotate3dX(<float> radians)
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_ROTATE3DX 
mat3 rotate3dX(const in float r){
    float c = cos(r);
    float s = sin(r);
    return mat3(vec3(1.0,0.0,0.0),
                vec3(0.0,c,s),
                vec3(0.0,-s,c));
}
/*
contributors: Patricio Gonzalez Vivo
description: returns a 3x3 rotation matrix
use: <mat3> rotate3dY(<float> radians)
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_ROTATE3DY 
mat3 rotate3dY(const in float r){
    float c = cos(r);
    float s = sin(r);
    return mat3(vec3(c,0.,-s),
                vec3(0.,1.,0.),
                vec3(s,0.,c));
}
/*
contributors: Patricio Gonzalez Vivo
description: returns a 3x3 rotation matrix
use: <mat3> rotate3dZ(<float> radians)
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_ROTATE3DZ 
mat3 rotate3dZ(const in float r){
    float c = cos(r);
    float s = sin(r);
    return mat3(vec3(c,s,0.),
                vec3(-s,c,0.),
                vec3(0.,0.,1.));
}
/*
contributors: Patricio Gonzalez Vivo
description: 'Fix the aspect ratio of a space keeping things squared for you.'
use: <vec2> aspect(<vec2> st, <vec2> st_size)
examples:
    - https://raw.githubusercontent.com/patriciogonzalezvivo/lygia_examples/main/draw_shapes.frag
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_ASPECT 
vec2 aspect(vec2 st, vec2 s) {
    st.x = st.x * (s.x / s.y);
    return st;
}
/*
contributors: [Ivan Dianov, Kathy McGuiness]
description: cartesian to polar transformation.
use: <vec2|vec3> cart2polar(<vec2|vec3> st)
*/
#define FNC_CART2POLAR 
vec2 cart2polar(in vec2 st) {
    return vec2(atan(st.y, st.x), length(st));
}
// https://mathworld.wolfram.com/SphericalCoordinates.html
vec3 cart2polar( in vec3 st ) {
    float r = length(st);
    float phi = acos(st.z/r);
    float theta = atan(st.y, st.x);
    return vec3(r, phi, theta);
}
/*
contributors: Patricio Gonzalez Vivo
description: "It center the coordinates from 0 to 1 to -1 to 1\nSo the center goes\
    \ from 0.5 to 0.0. \n"
use: <float|vec2|vec3> center(<float|vec2|vec3> st)
examples:
    - https://raw.githubusercontent.com/patriciogonzalezvivo/lygia_examples/main/draw_shapes.frag
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_CENTER 
float center(float x) { return x * 2.0 - 1.0; }
vec2 center(vec2 v) { return v * 2.0 - 1.0; }
vec3 center(vec3 v) { return v * 2.0 - 1.0; }
/*
contributors: [Ivan Dianov, Shadi El Hajj]
description: polar to cartesian conversion.
use: polar2cart(<vec2> polar)
*/
#define FNC_POLAR2CART 
vec2 polar2cart(in vec2 polar) {
    return vec2(cos(polar.x), sin(polar.x)) * polar.y;
}
// https://mathworld.wolfram.com/SphericalCoordinates.html
vec3 polar2cart( in float r, in float phi, in float theta) {
    float x = r * cos(theta) * sin(phi);
    float y = r * sin(theta) * sin(phi);
    float z = r * cos(phi);
    return vec3(x, y, z);
}
#define BLOOM_QUALITY 1
#define FNC_BLOOM 
vec3 bloom(sampler2D tex, float bloomSpread, float lod)
{
    vec2 pos = gl_FragCoord.xy / RENDERSIZE;
    vec4 bloom = vec4(0);
    for (int y = -BLOOM_QUALITY; y <= BLOOM_QUALITY; y++)
    for (int x = -BLOOM_QUALITY; x <= BLOOM_QUALITY; x++) {
        bloom += textureLod(tex, pos + (vec2(x, y) / RENDERSIZE) * bloomSpread, lod);
    }
    return (bloom / bloom.a).rgb;
}
vec2 seed = vec2(0);
float frand(void)
{
    seed += vec2(1.153535, -1.1231354);
    return random(seed);
}
float TIME_SCALED = TIME * formationSpeed;
Medium raymarchVolumeMap(vec3 position)
{
    float r = length(position);
    if (r > outerRadius)
        return mediumNew(volumeColor.rgb, r - innerRadius);
    float power = powerAmplitude * sin(TIME_SCALED * 0.1);
    vec3 z = position;
    float dr = 1.;
    for (int i = 0; i < 6; i++) {
        vec3 polar = cart2polar(z.xzy);
        r = polar.x;
        if (r > outerRadius)
            break;
        polar.yz = polar.yz * power - TIME_SCALED;
        z = polar2cart(pow(r, power), polar.y, polar.z) + position;
        dr = pow(r, power - 1.) * power * dr + 1.;
    }
    return mediumNew(volumeColor.rgb, 0.5 * log(r) * r / dr);
}
vec3 raymarchVolumeShadowTransmittance(vec3 position, vec3 rayDirectionL, float stepSizeL)
{
    vec3 transmittanceL = vec3(1.0, 1.0, 1.0);
    float tL = 0.0;
    for (int i = 0; i < RAYMARCH_VOLUME_SAMPLES_LIGHT; i++) {
        vec3 positionL = position + rayDirectionL * tL;
        Medium resL = raymarchVolumeMap(positionL);
        float densityL = -resL.sdf;
        tL -= max(resL.sdf, stepSizeL);
        positionL = position + rayDirectionL * tL;
        if (resL.sdf < stepSizeL) {
            float offset = frand() * stepSizeL * RAYMARCH_VOLUME_DITHER;
            tL += stepSizeL - offset;
            positionL = position + rayDirectionL * tL;
            if (densityL > 0.) {
                transmittanceL *= exp(-absorbanceFactor * offset);
                if (mmax(transmittanceL) < 0.1)
                    break;
            }
        }
        if (length(position) > outerRadius)
            break;
    }
    return transmittanceL;
}
vec3 raymarchVolume(vec3 rayOrigin, vec3 rayDirection, vec2 st, float minDist, vec3 background)
{
    vec3 scatteredLuminance = vec3(0.0, 0.0, 0.0);
    vec3 transmittance = vec3(1.0, 1.0, 1.0);
    float stepSize = RAYMARCH_MAX_DIST/float(RAYMARCH_VOLUME_SAMPLES);
    float t = RAYMARCH_MIN_DIST;
    for (int i = 0; i < RAYMARCH_VOLUME_SAMPLES; i++) {
        vec3 position = rayOrigin + rayDirection * t;
        Medium res = raymarchVolumeMap(position);
        float density = -res.sdf;
        vec3 extinction = res.absorption + res.scattering;
        t += max(res.sdf, stepSize);
        position = rayOrigin + rayDirection * t;
        if (length(position) < outerRadius) {
            if (res.sdf < stepSize) {
                float offset = frand() * stepSize * RAYMARCH_VOLUME_DITHER;
                t += -stepSize + offset;
                position = rayOrigin + rayDirection * t;
                if (density > 0.) {
                    float absorbance = exp(-absorbanceFactor * offset);
                    if (density < 0.0005)
                        scatteredLuminance += transmittance * highlightColor.rgb;
                    if (frand() < 1. / attenuationL) {
                        float stepSizeL = 0.2; // RAYMARCH_MAX_DIST/float(RAYMARCH_VOLUME_SAMPLES_LIGHT);
                        vec3 rayDirectionL = normalize(LIGHT_DIRECTION);
                        vec3 shadow = raymarchVolumeShadowTransmittance(position, rayDirectionL, stepSizeL);
                        vec3 L = lightColor.rgb * lightIntensity;
                        scatteredLuminance += attenuationL * shadow * transmittance * res.scattering * (1. - absorbance) * L;
                    }
                    if (mmax(transmittance) < 0.05) {
                        transmittance = vec3(0);
                        break;
                    }
                    if (frand() > absorbance) {
                        rayDirection = vec3(1, 0, 0) * rotate3dZ(-frand() * TWO_PI) * rotate3dX(-frand() * TWO_PI); // random direction
                        transmittance *= extinction;
                    }
                }
            }
        } else if (dot(rayDirection, position) > 0.) {
            break;
        }
    }
    return background * transmittance + scatteredLuminance;
}
vec2 sampleAperture(int nbBlades, float rotation)
{
    float alpha = TWO_PI / float(nbBlades);
    float side = sin(alpha * 0.5);
    int blade = int(frand() * float(nbBlades));
    vec2 tri = vec2(frand(), -frand());
    if (tri.x + tri.y > 0.)
        tri = vec2(tri.x - 1., -1. - tri.y);
    tri.x *= side;
    tri.y *= sqrt(1. - side*side);
    return tri * rotate2d(rotation * DEG2RAD + float(blade) / float(nbBlades) * TWO_PI);
}
// The Shadertoy shader uses the direction argument to return the color from a
// cubemap, which is impossible in an ISF shader.
vec3 backgroundColor(vec3 direction)
{
    return vec3(0);
}
void main()
{
    if (PASSINDEX == 0) // Shadertoy Buffer A
    {
        vec2 uv = gl_FragCoord.xy / RENDERSIZE;
        seed = uv * 1000. + log(vec2(FRAMEINDEX));
        uv += positionRandomness * vec2(frand(), frand()) / RENDERSIZE;
        uv = 0.5 * aspect(center(uv), RENDERSIZE);
        vec3 focalPoint = vec3(uv * cameraFocalDistance / cameraFocalLength, cameraFocalDistance);
        vec3 aperture = cameraAperture * vec3(sampleAperture(6, apertureRotation), 0.);
        float rotation = TIME_SCALED * rotationSpeed;
        mat3 cameraMatrix = rotate3dY(rotation);
        vec3 cameraPosition = vec3(0, 0, -2.5) * cameraMatrix;
        cameraMatrix = rotate3dZ(-0.5 * sin(TIME_SCALED * 0.3)) * cameraMatrix;
        vec3 rayDirection = normalize(focalPoint - aperture) * cameraMatrix;
        float minDist = 1./0.; // Not used
        gl_FragColor = vec4(raymarchVolume(cameraPosition + aperture * cameraMatrix, rayDirection, uv, minDist, backgroundColor(rayDirection)), 1);
        if (FRAMEINDEX > 0)
            gl_FragColor += IMG_THIS_PIXEL(cloud) * motionBlur;
    }
    else // Shadertoy Image
    {
        vec4 color = IMG_THIS_PIXEL(cloud);
        color /= color.a;
        color.rgb += 0.25 * bloom(cloud, bloomSpread, 7.);
        if (enableTonemap)
            color.rgb = tonemapACES(color.rgb);
        gl_FragColor = vec4(color.rgb, length(color.rgb));
    }
}
'''


class IsfStrangeCloud(GlslSource):
    name = 'isf_strange_cloud'
    label = 'Strange Cloud'
    description = 'Fractal cloud, converted from <https://www.shadertoy.com/view/tsGSDt>'
    dialect = 'isf'
    GLSL = GLSL
    params = scene_params({
        "formation_speed": {
            "type": "number",
            "minimum": -10.0,
            "maximum": 10.0,
            "default": 0.9,
            "x-ui": {
                "label": "Formation speed",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "formationSpeed"
            }
        },
        "power_amplitude": {
            "type": "number",
            "minimum": -20.0,
            "maximum": 20.0,
            "default": 5.0,
            "x-ui": {
                "label": "Power amplitude",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "powerAmplitude"
            }
        },
        "absorbance_factor": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
            "default": 10.0,
            "x-ui": {
                "label": "Absorbance factor",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "absorbanceFactor"
            }
        },
        "outer_radius": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.5,
            "x-ui": {
                "label": "Outer radius",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "outerRadius"
            }
        },
        "inner_radius": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.2,
            "x-ui": {
                "label": "Inner radius",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "innerRadius"
            }
        },
        "rotation_speed": {
            "type": "number",
            "minimum": -10.0,
            "maximum": 10.0,
            "default": 0.2,
            "x-ui": {
                "label": "Rotation speed",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "rotationSpeed"
            }
        },
        "motion_blur": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.9,
            "x-ui": {
                "label": "Motion blur",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "motionBlur"
            }
        },
        "camera_focal_distance": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.6,
            "x-ui": {
                "label": "Camera focal distance",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "cameraFocalDistance"
            }
        },
        "camera_focal_length": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.0,
            "x-ui": {
                "label": "Camera focal length",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "cameraFocalLength"
            }
        },
        "camera_aperture": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.075,
            "x-ui": {
                "label": "Camera aperture",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "cameraAperture"
            }
        },
        "aperture_rotation": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.075,
            "x-ui": {
                "label": "Aperture rotation",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "apertureRotation"
            }
        },
        "volume_color": {
            "type": "string",
            "format": "color",
            "default": "#4c4c4c",
            "x-ui": {
                "label": "Volume color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "volumeColor",
                "color": 4,
                "alpha": 1.0
            }
        },
        "light_radius": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 200.0,
            "default": 3.3166247904,
            "x-ui": {
                "label": "Light distance",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "lightRadius"
            }
        },
        "light_phi": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 180.0,
            "default": 72.4515993862,
            "x-ui": {
                "label": "Light phi (degrees)",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "lightPhi"
            }
        },
        "light_theta": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 360.0,
            "default": 251.5650511771,
            "x-ui": {
                "label": "Light theta (degrees)",
                "step": 1.0,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "lightTheta"
            }
        },
        "light_color": {
            "type": "string",
            "format": "color",
            "default": "#8080b2",
            "x-ui": {
                "label": "Light color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "lightColor",
                "color": 4,
                "alpha": 1.0
            }
        },
        "light_intensity": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
            "default": 20.0,
            "x-ui": {
                "label": "Light intensity",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "lightIntensity"
            }
        },
        "attenuation_l": {
            "type": "number",
            "minimum": 0.01,
            "maximum": 5.0,
            "default": 4.0,
            "x-ui": {
                "label": "Light attenuation",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "attenuationL"
            }
        },
        "highlight_color": {
            "type": "string",
            "format": "color",
            "default": "#801a33",
            "x-ui": {
                "label": "Highlight color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "highlightColor",
                "color": 4,
                "alpha": 1.0
            }
        },
        "enable_bloom": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Enable bloom"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "enableBloom"
            }
        },
        "bloom_spread": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
            "default": 2.0,
            "x-ui": {
                "label": "Bloom spread",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "bloomSpread"
            }
        },
        "enable_tonemap": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Enable tonemap"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "enableTonemap"
            }
        },
        "position_randomness": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Position randomness",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "positionRandomness"
            }
        }
    }, render_scale=0.25)
