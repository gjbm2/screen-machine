"""Endless living creature: Weird endless living creature, converted from <https://www.shadertoy.com/view/tljXWy>

Source: https://github.com/nwhetsell/isf-shaders/blob/HEAD/Endless%20living%20creature.fs
Author: Leon Denise <https://www.shadertoy.com/user/leon>
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
    "CREDIT": "Leon Denise <https://www.shadertoy.com/user/leon>",
    "DESCRIPTION": "Weird endless living creature, converted from <https://www.shadertoy.com/view/tljXWy>",
    "INPUTS": [
        {
            "NAME": "sphereCount",
            "LABEL": "Sphere count",
            "TYPE": "float",
            "DEFAULT": 15,
            "MAX": 100,
            "MIN": 1
        },
        {
            "NAME": "speed",
            "LABEL": "Speed",
            "TYPE": "float",
            "DEFAULT": 1,
            "MAX": 10,
            "MIN": 0
        },
        {
            "NAME": "scrollSpeed",
            "LABEL": "Scroll speed",
            "TYPE": "float",
            "DEFAULT": 1,
            "MAX": 2,
            "MIN": 0
        },
        {
            "NAME": "repeatAmount",
            "LABEL": "Repeat amount",
            "TYPE": "float",
            "DEFAULT": 5,
            "MAX": 50,
            "MIN": 0
        },
        {
            "NAME": "balance",
            "LABEL": "Balance",
            "TYPE": "float",
            "DEFAULT": 1.5,
            "MAX": 10,
            "MIN": 0
        },
        {
            "NAME": "range",
            "LABEL": "Range",
            "TYPE": "float",
            "DEFAULT": 1.4,
            "MAX": 10,
            "MIN": 0
        },
        {
            "NAME": "radius",
            "LABEL": "Radius",
            "TYPE": "float",
            "DEFAULT": 0.6,
            "MAX": 10,
            "MIN": 0
        },
        {
            "NAME": "blend",
            "LABEL": "Blend",
            "TYPE": "float",
            "DEFAULT": 0.3,
            "MAX": 10,
            "MIN": 0
        },
        {
            "NAME": "falloff",
            "LABEL": "Fall-off",
            "TYPE": "float",
            "DEFAULT": 1.2,
            "MAX": 10,
            "MIN": 0
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
            "DEFAULT": [0, 0],
            "MIN": [-1, -1],
            "MAX": [1, 1]
        },
        {
            "NAME": "motion_frames",
            "LABEL": "Motion frames",
            "TYPE": "float",
            "DEFAULT": 1,
            "MAX": 10,
            "MIN": 1
        },
        {
            "NAME": "mainColor",
            "LABEL": "Main color",
            "TYPE": "color",
            "DEFAULT": [0.7, 0.8, 0.9, 1]
        },
        {
            "NAME": "highlightColor",
            "LABEL": "Highlight color",
            "TYPE": "color",
            "DEFAULT": [0.8, 0.6, 0.5, 1]
        }
    ],
    "ISFVSN": "2"
}*/
// #define ISF_EDITOR_WEBSITE
// #define RANDOM_HIGHER_RANGE
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
#define RANDOM_SCALE vec4(443.897, 441.423, .0973, .1099)
#define FNC_RANDOM 
float random(in float x) {
    return fract(sin(x) * 43758.5453);
}
float random(in vec2 st) {
    return fract(sin(dot(st.xy, vec2(12.9898, 78.233))) * 43758.5453);
}
float random(in vec3 pos) {
    return fract(sin(dot(pos.xyz, vec3(70.9898, 78.233, 32.4355))) * 43758.5453123);
}
float random(in vec4 pos) {
    float dot_product = dot(pos, vec4(12.9898,78.233,45.164,94.673));
    return fract(sin(dot_product) * 43758.5453);
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
contributors: Patricio Gonzalez Vivo
description: Convert from gamma to linear color space.
use: gamma2linear(<float|vec3|vec4> color)
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define GAMMA 2.2
#define FNC_GAMMA2LINEAR 
float gamma2linear(const in float v) {
    return pow(v, GAMMA);
}
vec3 gamma2linear(const in vec3 v) {
    return pow(v, vec3(GAMMA));
}
vec4 gamma2linear(const in vec4 v) {
    return vec4(gamma2linear(v.rgb), v.a);
}
/*
contributors: Patricio Gonzalez Vivo
description: It defines the default sampler type and function for the shader based on the version of GLSL.
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define SAMPLER_FNC(TEX,UV) texture(TEX, UV)
#define SAMPLER_TYPE sampler2D

/*
contributors: Patricio Gonzalez Vivo
description: Get material BaseColor from GlslViewer's defines https://github.com/patriciogonzalezvivo/glslViewer/wiki/GlslViewer-DEFINES#material-defines
use: vec4 materialAlbedo()
options:
    - SAMPLER_FNC(TEX, UV): optional depending the target version of GLSL (texture2D(...) or texture(...))
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_MATERIAL_ALBEDO 
vec4 materialAlbedo() {
    vec4 albedo = vec4(0.5, 0.5, 0.5, 1.0);
    return albedo;
}
/*
contributors: Patricio Gonzalez Vivo
description: It defines the default sampler type and function for the shader based on the version of GLSL.
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/

/*
contributors: Patricio Gonzalez Vivo
description: Get material specular property from GlslViewer's defines https://github.com/patriciogonzalezvivo/glslViewer/wiki/GlslViewer-DEFINES#material-defines
use: vec4 materialMetallic()
options:
    - SAMPLER_FNC(TEX, UV): optional depending the target version of GLSL (texture2D(...) or texture(...))
    - MATERIAL_SPECULARMAP
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_MATERIAL_SPECULAR 
vec3 materialSpecular() {
    vec3 spec = vec3(0.04);
    return spec;
}
/*
contributors: Patricio Gonzalez Vivo
description: Convert from gamma to linear color space.
use: gamma2linear(<float|vec3|vec4> color)
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
/*
contributors: Patricio Gonzalez Vivo
description: It defines the default sampler type and function for the shader based on the version of GLSL.
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/

/*
contributors: Patricio Gonzalez Vivo
description: Get material emissive property from GlslViewer's defines https://github.com/patriciogonzalezvivo/glslViewer/wiki/GlslViewer-DEFINES#material-defines
use: vec4 materialEmissive()
options:
    - SAMPLER_FNC(TEX, UV): optional depending the target version of GLSL (texture2D(...) or texture(...))
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_MATERIAL_EMISSIVE 
vec3 materialEmissive() {
    vec3 emission = vec3(0.0);
    return emission;
}
/*
contributors: Patricio Gonzalez Vivo
description: It defines the default sampler type and function for the shader based on the version of GLSL.
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/

/*
contributors: Patricio Gonzalez Vivo
description: Get material normal property from GlslViewer's defines https://github.com/patriciogonzalezvivo/glslViewer/wiki/GlslViewer-DEFINES#material-defines
use: vec4 materialOcclusion()
options:
    - SAMPLER_FNC(TEX, UV): optional depending the target version of GLSL (texture2D(...) or texture(...))
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_MATERIAL_OCCLUSION 
float materialOcclusion() {
    float occlusion = 1.0;
    return occlusion;
}

/*
contributors: Patricio Gonzalez Vivo
description: It defines the default sampler type and function for the shader based on the version of GLSL.
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/

/*
contributors: Patricio Gonzalez Vivo
description: Get material normal property from GlslViewer's defines https://github.com/patriciogonzalezvivo/glslViewer/wiki/GlslViewer-DEFINES#material-defines
use: vec4 materialNormal()
options:
    - SAMPLER_FNC(TEX, UV): optional depending the target version of GLSL (texture2D(...) or texture(...))
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_MATERIAL_NORMAL 
vec3 materialNormal() {
    vec3 normal = vec3(0.0, 0.0, 1.0);
    return normal;
}

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
/*
contributors: Patricio Gonzalez Vivo
description: Convert diffuse/specular/glossiness workflow to PBR metallic factor
use: <float> toMetallic(<vec3> diffuse, <vec3> specular, <float> maxSpecular)
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define TOMETALLIC_MIN_REFLECTANCE 0.04
#define FNC_TOMETALLIC 
float toMetallic(const in vec3 diffuse, const in vec3 specular, const in float maxSpecular) {
    float perceivedDiffuse = sqrt(0.299 * diffuse.r * diffuse.r + 0.587 * diffuse.g * diffuse.g + 0.114 * diffuse.b * diffuse.b);
    float perceivedSpecular = sqrt(0.299 * specular.r * specular.r + 0.587 * specular.g * specular.g + 0.114 * specular.b * specular.b);
    if (perceivedSpecular < TOMETALLIC_MIN_REFLECTANCE) {
        return 0.0;
    }
    float a = TOMETALLIC_MIN_REFLECTANCE;
    float b = perceivedDiffuse * (1.0 - maxSpecular) / (1.0 - TOMETALLIC_MIN_REFLECTANCE) + perceivedSpecular - 2.0 * TOMETALLIC_MIN_REFLECTANCE;
    float c = TOMETALLIC_MIN_REFLECTANCE - perceivedSpecular;
    float D = max(b * b - 4.0 * a * c, 0.0);
    return saturate((-b + sqrt(D)) / (2.0 * a));
}
float toMetallic(const in vec3 diffuse, const in vec3 specular) {
    float maxSpecula = max(max(specular.r, specular.g), specular.b);
    return toMetallic(diffuse, specular, maxSpecula);
}
/*
contributors: Patricio Gonzalez Vivo
description: Convert from gamma to linear color space.
use: gamma2linear(<float|vec3|vec4> color)
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
/*
contributors: Patricio Gonzalez Vivo
description: It defines the default sampler type and function for the shader based on the version of GLSL.
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/

/*
contributors: Patricio Gonzalez Vivo
description: Get material BaseColor from GlslViewer's defines https://github.com/patriciogonzalezvivo/glslViewer/wiki/GlslViewer-DEFINES#material-defines
use: vec4 materialAlbedo()
options:
    - SAMPLER_FNC(TEX, UV): optional depending the target version of GLSL (texture2D(...) or texture(...))
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
/*
contributors: Patricio Gonzalez Vivo
description: It defines the default sampler type and function for the shader based on the version of GLSL.
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/

/*
contributors: Patricio Gonzalez Vivo
description: Get material specular property from GlslViewer's defines https://github.com/patriciogonzalezvivo/glslViewer/wiki/GlslViewer-DEFINES#material-defines
use: vec4 materialMetallic()
options:
    - SAMPLER_FNC(TEX, UV): optional depending the target version of GLSL (texture2D(...) or texture(...))
    - MATERIAL_SPECULARMAP
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
/*
contributors: Patricio Gonzalez Vivo
description: It defines the default sampler type and function for the shader based on the version of GLSL.
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/

/*
contributors: Patricio Gonzalez Vivo
description: Get material metallic property from GlslViewer's defines https://github.com/patriciogonzalezvivo/glslViewer/wiki/GlslViewer-DEFINES#material-defines
use: vec4 materialMetallic()
options:
    - SAMPLER_FNC(TEX, UV): optional depending the target version of GLSL (texture2D(...) or texture(...))
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_MATERIAL_METALLIC 
float materialMetallic() {
    float metallic = 0.0;
    vec3 diffuse = materialAlbedo().rgb;
    vec3 specular = materialSpecular();
    metallic = toMetallic(diffuse, specular);
    return metallic;
}
/*
contributors: Patricio Gonzalez Vivo
description: It defines the default sampler type and function for the shader based on the version of GLSL.
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/

/*
contributors: Patricio Gonzalez Vivo
description: Get material roughness property from GlslViewer's defines https://github.com/patriciogonzalezvivo/glslViewer/wiki/GlslViewer-DEFINES#material-defines
use: vec4 materialRoughness()
options:
    - SAMPLER_FNC(TEX, UV): optional depending the target version of GLSL (texture2D(...) or texture(...))
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_MATERIAL_ROUGHNESS 
float materialRoughness() {
    float roughness = 0.05;
    return roughness;
}

/*
contributors: Patricio Gonzalez Vivo
description: Convertes from PBR roughness/metallic to a shininess factor (typaclly use on diffuse/specular/ambient workflow)
use: float toShininess(<float> roughness, <float> metallic)
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_TOSHININESS 
float toShininess(const in float roughness, const in float metallic) {
    float s = .95 - roughness * 0.5;
    s *= s;
    s *= s;
    return s * (80.0 + 160.0 * (1.0-metallic));
}

/*
contributors: Patricio Gonzalez Vivo
description: Get material shininess property from GlslViewer's defines https://github.com/patriciogonzalezvivo/glslViewer/wiki/GlslViewer-DEFINES#material-defines
use: vec4 materialShininess()
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_MATERIAL_SHININESS 
float materialShininess() {
    float shininess = 15.0;
    return shininess;
}

/*
contributors: Patricio Gonzalez Vivo
description: Generic Material Structure
options:
    - SCENE_BACK_SURFACE
    - SHADING_MODEL_CLEAR_COAT
    - MATERIAL_HAS_CLEAR_COAT_NORMAL
    - SHADING_MODEL_IRIDESCENCE
    - SHADING_MODEL_SUBSURFACE
    - SHADING_MODEL_CLOTH
    - SHADING_MODEL_SPECULAR_GLOSSINESS
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define RENDER_RAYMARCHING 
#define SHADING_MODEL_CLEAR_COAT 
#define STR_MATERIAL 
struct Material {
    vec4 albedo;
    vec3 emissive;
    vec3 position; // world position of the surface
    vec3 normal; // world normal of the surface
    float sdf;
    bool valid;
    vec3 ior; // Index of Refraction
    float roughness;
    float metallic;
    float reflectance;
    float ambientOcclusion; // default 1.0
    float clearCoat;
    float clearCoatRoughness;
};

/*
contributors:  Inigo Quiles
description: Union operation of two SDFs 
use: <float> opUnion( in <float|vec4> d1, in <float|vec4> d2 [, <float> smooth_factor] ) 
*/
#define FNC_OPUNION 
float opUnion( float d1, float d2 ) { return min(d1, d2); }
Material opUnion( Material d1, Material d2 ) {
    if (d1.sdf < d2.sdf) {
        return d1;
    } else {
        return d2;
    }
}
// Soft union
float opUnion( float d1, float d2, float k ) {
    float h = saturate( 0.5 + 0.5*(d2-d1)/k );
    return mix( d2, d1, h ) - k*h*(1.0-h);
}
vec4 opUnion( vec4 d1, vec4 d2, float k ) {
    float h = saturate( 0.5 + 0.5*(d2.a - d1.a)/k );
    vec4 result = mix(d2, d1, h);
    result.a -= k * h * (1.0 - h);
    return result;
}
/*
contributors:  Inigo Quiles
description: generate the SDF of a sphere
use: <float> sphereSDF( in <vec3> pos[], in <float> size] ) 
*/
#define FNC_SPHERESDF 
float sphereSDF(vec3 p) { return length(p); }
float sphereSDF(vec3 p, float s) { return sphereSDF(p) - s; }
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
contributors: Patricio Gonzalez Vivo
description: create a look at matrix. Right handed by default.
use:
    - <mat3> lookAt(<vec3> forward, <vec3> up)
    - <mat3> lookAt(<vec3> eye, <vec3> target, <vec3> up)
    - <mat3> lookAt(<vec3> eye, <vec3> target, <float> roll)
    - <mat3> lookAt(<vec3> forward)
options:
    - LOOK_AT_LEFT_HANDED: assume a left-handed coordinate system
    - LOOK_AT_RIGHT_HANDED: assume a right-handed coordinate system
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_LOOKAT 
mat3 lookAt(vec3 forward, vec3 up) {
    vec3 zaxis = normalize(forward);
    vec3 xaxis = normalize(cross(up, zaxis));
    vec3 yaxis = cross(zaxis, xaxis);
    return mat3(xaxis, yaxis, zaxis);
}
mat3 lookAt(vec3 eye, vec3 target, vec3 up) {
    vec3 forward = normalize(target - eye);
    return lookAt(forward, up);
}
mat3 lookAt(vec3 eye, vec3 target, float roll) {
    vec3 up = vec3(sin(roll), cos(roll), 0.0);
    return lookAt(eye, target, up);
}
mat3 lookAt(vec3 forward) {
    return lookAt(forward, vec3(0.0, 1.0, 0.0));
}
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

#define FNC_OPREPEAT_ADDITIONS 
float opRepeat( in float p, in float s ) {
    return mod(p+s*0.5,s)-s*0.5;
}
vec2 opRepeat( in vec2 p, in vec2 s ) {
    return mod(p+s*0.5,s)-s*0.5;
}
// Weird endless living creature
// inspired by Inigo Quilez live stream shader deconstruction
// Leon Denise (ponk) 2019.08.28
// Licensed under hippie love conspiracy
// Using code from
// Inigo Quilez
// Morgan McGuire
float geometry(vec3 pos, float time)
{
    float scene = 1.;
    float a = 1.;
    float t = time * 0.5 + pos.x / 30.;
    t = floor(t) + smoothstep(0., 0.9, pow(fract(t), 2.));
    pos.x = opRepeat(pos.x + TIME * scrollSpeed, repeatAmount);
    for (int i = int(sphereCount); i > 0; --i) {
        pos.x = abs(pos.x) - range * a;
        vec2 angles = polar2cart(vec2(t, balance / a)) + a * 2.;
        pos.xy *= rotate2d(-angles.x);
        pos.zy *= rotate2d(-angles.y);
        scene = opUnion(scene, sphereSDF(pos, radius * a), blend * a);
        a /= falloff;
    }
    return scene;
}
float raymarch(vec3 eye, vec3 ray, float time, out float total)
{
    float dither = random(ray.xy + fract(time));
    total = 0.;
    const int count = 20;
    for (int index = count; index > 0; index--) {
        float dist = geometry(eye + total * ray, time);
        dist *= 0.9 + 0.1 * dither;
        total += dist;
        if (dist < 0.001 * total) {
            return float(index) / float(count);
        }
    }
    return 0.;
}
vec3 camera(vec3 eye)
{
    if (enableMouse) {
        eye.yz *= rotate2d(-mouse.y * PI);
        eye.xz *= rotate2d(-mouse.x * PI);
    }
    return eye;
}
void main()
{
    vec2 uv = aspect(center(gl_FragCoord.xy / RENDERSIZE), RENDERSIZE);
    vec3 eye = camera(vec3(0, 0, 4));
    mat3 lookMatrix = lookAt(eye, vec3(0), vec3(0, 1, 0));
    vec3 ray = normalize(lookMatrix[0] * uv.x + lookMatrix[1] * uv.y + lookMatrix[2]);
    float total = 0.;
    gl_FragColor = vec4(0);
    for (float index = motion_frames; index > 0.; index--) {
        float dither = random(ray.xy + fract(TIME + index));
        float time = TIME * speed + (dither + index) / (10. * motion_frames);
        gl_FragColor += vec4(raymarch(eye, ray, time, total)) / motion_frames;
    }
    // extra color
    gl_FragColor *= mainColor;
    gl_FragColor += highlightColor * smoothstep(7., 0., total);
    if (any(greaterThan(gl_FragColor.rgb, vec3(0)))) {
        gl_FragColor.a = 1.;
    }
}
'''


class IsfEndlessLivingCreature(GlslSource):
    name = 'isf_endless_living_creature'
    label = 'Endless living creature'
    description = 'Weird endless living creature, converted from <https://www.shadertoy.com/view/tljXWy>'
    dialect = 'isf'
    GLSL = GLSL
    params = scene_params({
        "sphere_count": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 100.0,
            "default": 15.0,
            "x-ui": {
                "label": "Sphere count",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "sphereCount"
            }
        },
        "p_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.0,
            "x-ui": {
                "label": "Speed",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "speed"
            }
        },
        "scroll_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 1.0,
            "x-ui": {
                "label": "Scroll speed",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "scrollSpeed"
            }
        },
        "repeat_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 50.0,
            "default": 5.0,
            "x-ui": {
                "label": "Repeat amount",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "repeatAmount"
            }
        },
        "balance": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.5,
            "x-ui": {
                "label": "Balance",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "balance"
            }
        },
        "range": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.4,
            "x-ui": {
                "label": "Range",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "range"
            }
        },
        "radius": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 0.6,
            "x-ui": {
                "label": "Radius",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "radius"
            }
        },
        "blend": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 0.3,
            "x-ui": {
                "label": "Blend",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "blend"
            }
        },
        "falloff": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.2,
            "x-ui": {
                "label": "Fall-off",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "falloff"
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
            "minimum": -1.0,
            "maximum": 1.0,
            "default": 0.0,
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
            "minimum": -1.0,
            "maximum": 1.0,
            "default": 0.0,
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
        "motion_frames": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 10.0,
            "default": 1.0,
            "x-ui": {
                "label": "Motion frames",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "motion_frames"
            }
        },
        "main_color": {
            "type": "string",
            "format": "color",
            "default": "#b2cce6",
            "x-ui": {
                "label": "Main color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mainColor",
                "color": 4,
                "alpha": 1.0
            }
        },
        "highlight_color": {
            "type": "string",
            "format": "color",
            "default": "#cc9980",
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
        }
    }, render_scale=0.25)
