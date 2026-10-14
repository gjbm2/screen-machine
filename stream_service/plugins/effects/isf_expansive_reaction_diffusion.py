"""Expansive reaction diffusion: Reaction-diffusion system, converted from <https://www.shadertoy.com/view/4dcGW2>

Source: https://github.com/nwhetsell/isf-shaders/blob/HEAD/expansive%20reaction-diffusion.fs
Author: Flexi <https://www.shadertoy.com/user/Flexi>
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
    "CREDIT": "Flexi <https://www.shadertoy.com/user/Flexi>",
    "DESCRIPTION": "Reaction-diffusion system, converted from <https://www.shadertoy.com/view/4dcGW2>",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "NAME": "gradientDistance",
            "LABEL": "Gradient distance",
            "TYPE": "float",
            "DEFAULT": 4,
            "MIN": 1,
            "MAX": 10
        },
        {
            "NAME": "diffusionFactor",
            "LABEL": "Diffusion factor",
            "TYPE": "float",
            "DEFAULT": 1,
            "MIN": 0,
            "MAX": 2
        },
        {
            "NAME": "reactionDiffusionFactor",
            "LABEL": "Reaction-diffusion factor",
            "TYPE": "float",
            "DEFAULT": 0.047,
            "MIN": 0,
            "MAX": 1
        },
        {
            "NAME": "decayFactor",
            "LABEL": "Decay factor",
            "TYPE": "float",
            "DEFAULT": 0.0025,
            "MIN": 0,
            "MAX": 1
        },
        {
            "NAME": "gaussianKernelScale",
            "LABEL": "Gaussian kernel scale",
            "TYPE": "float",
            "DEFAULT": 1.44,
            "MIN": -10,
            "MAX": 10
        },
        {
            "NAME": "standardDeviationScale",
            "LABEL": "Standard deviation scale",
            "TYPE": "float",
            "DEFAULT": 0.2913965934,
            "MIN": 0,
            "MAX": 10
        },
        {
            "NAME": "showLightWithInputImage",
            "LABEL": "Show light with input image",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "showLightWithMouse",
            "LABEL": "Show light with mouse",
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
            "NAME": "inverseLightSize",
            "LABEL": "Light size",
            "TYPE": "float",
            "DEFAULT": 0.25,
            "MIN": 0,
            "MAX": 10
        },
        {
            "NAME": "radiance",
            "LABEL": "Radiance",
            "TYPE": "float",
            "DEFAULT": 4,
            "MIN": 0,
            "MAX": 10
        }
    ],
    "ISFVSN": "2",
    "PASSES": [
        {
            "TARGET": "bufferA",
            "PERSISTENT": true,
            "FLOAT": true
        },
        {
            "TARGET": "bufferB",
            "PERSISTENT": true,
            "FLOAT": true
        },
        {
            "TARGET": "bufferC",
            "PERSISTENT": true,
            "FLOAT": true
        },
        {

        }
    ]
}*/
// #define ISF_EDITOR_WEBSITE
/*
contributor: nan
description: |
    Computes the luminance of the specified linear RGB color using the luminance coefficients from Rec. 709.
    Note, ThreeJS seems to inject this in all their shaders. Which could lead to issues
use: luminance(<vec3|vec4> color)
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
float luminance(in vec3 linear) { return dot(linear, vec3(0.21250175, 0.71537574, 0.07212251)); }
float luminance(in vec4 linear) { return luminance( linear.rgb ); }
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
    vec3 p3 = fract(vec3(p) * vec4(443.897, 441.423, .0973, .1099).xyz);
    p3 += dot(p3, p3.yzx + 19.19);
    return fract((p3.xx + p3.yz) * p3.zy);
}
vec2 random2(vec2 p) {
    vec3 p3 = fract(p.xyx * vec4(443.897, 441.423, .0973, .1099).xyz);
    p3 += dot(p3, p3.yzx + 19.19);
    return fract((p3.xx + p3.yz) * p3.zy);
}
vec2 random2(vec3 p3) {
    p3 = fract(p3 * vec4(443.897, 441.423, .0973, .1099).xyz);
    p3 += dot(p3, p3.yzx + 19.19);
    return fract((p3.xx + p3.yz) * p3.zy);
}
vec3 random3(float p) {
    vec3 p3 = fract(vec3(p) * vec4(443.897, 441.423, .0973, .1099).xyz);
    p3 += dot(p3, p3.yzx + 19.19);
    return fract((p3.xxy + p3.yzz) * p3.zyx);
}
vec3 random3(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * vec4(443.897, 441.423, .0973, .1099).xyz);
    p3 += dot(p3, p3.yxz + 19.19);
    return fract((p3.xxy + p3.yzz) * p3.zyx);
}
vec3 random3(vec3 p) {
    p = fract(p * vec4(443.897, 441.423, .0973, .1099).xyz);
    p += dot(p, p.yxz + 19.19);
    return fract((p.xxy + p.yzz) * p.zyx);
}
vec4 random4(float p) {
    vec4 p4 = fract(p * vec4(443.897, 441.423, .0973, .1099));
    p4 += dot(p4, p4.wzxy + 19.19);
    return fract((p4.xxyz + p4.yzzw) * p4.zywx);
}
vec4 random4(vec2 p) {
    vec4 p4 = fract(p.xyxy * vec4(443.897, 441.423, .0973, .1099));
    p4 += dot(p4, p4.wzxy + 19.19);
    return fract((p4.xxyz + p4.yzzw) * p4.zywx);
}
vec4 random4(vec3 p) {
    vec4 p4 = fract(p.xyzx * vec4(443.897, 441.423, .0973, .1099));
    p4 += dot(p4, p4.wzxy + 19.19);
    return fract((p4.xxyz + p4.yzzw) * p4.zywx);
}
vec4 random4(vec4 p4) {
    p4 = fract(p4 * vec4(443.897, 441.423, .0973, .1099));
    p4 += dot(p4, p4.wzxy + 19.19);
    return fract((p4.xxyz + p4.yzzw) * p4.zywx);
}
/*
contributors: Patricio Gonzalez Vivo
description: gaussian coefficient
use: <vec4|vec3|vec2|float> gaussian(<float> sigma, <vec4|vec3|vec2|float> d)
examples:
    - https://raw.githubusercontent.com/patriciogonzalezvivo/lygia_examples/main/math_gaussian.frag
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
float gaussian(float d, float s) { return exp(-(d*d) / (2.0 * s*s)); }
float gaussian( vec2 d, float s) { return exp(-( d.x*d.x + d.y*d.y) / (2.0 * s*s)); }
float gaussian( vec3 d, float s) { return exp(-( d.x*d.x + d.y*d.y + d.z*d.z ) / (2.0 * s*s)); }
float gaussian( vec4 d, float s) { return exp(-( d.x*d.x + d.y*d.y + d.z*d.z + d.w*d.w ) / (2.0 * s*s)); }
/*
contributors: Patricio Gonzalez Vivo
description: It defines the default sampler type and function for the shader based on the version of GLSL.
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/

/*
contributors: Patricio Gonzalez Vivo
description: fakes a clamp to edge texture
use: <vec4> sampleClamp2edge(<SAMPLER_TYPE> tex, <vec2> st [, <vec2> texResolution]);
options:
    - SAMPLER_FNC(TEX, UV)
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
vec4 sampleClamp2edge(sampler2D tex, vec2 st, vec2 texResolution) {
    vec2 pixel = 1.0/texResolution;
    return texture(tex, clamp(st, pixel, 1.0-pixel));
}
vec4 sampleClamp2edge(sampler2D tex, vec2 st) {
    return texture(tex, clamp(st, vec2(0.01), vec2(0.99) ));
}
vec4 sampleClamp2edge(sampler2D tex, vec2 st, float edge) {
    return texture(tex, clamp(st, vec2(edge), vec2(1.0 - edge) ));
}

/*
contributors: Patricio Gonzalez Vivo
description: One dimension Gaussian Blur to be applied in two passes
use: gaussianBlur1D(<SAMPLER_TYPE> texture, <vec2> st, <vec2> pixel_direction , const int kernelSize)
options:
    - SAMPLER_FNC(TEX, UV): optional depending the target version of GLSL (texture2D(...) or texture(...))
    - GAUSSIANBLUR1D_TYPE: null
    - GAUSSIANBLUR1D_SAMPLER_FNC(TEX, UV): null
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
/*
contributors: Patricio Gonzalez Vivo
description: some useful math constants
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
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
vec2 aspect(vec2 st, vec2 s) {
    st.x = st.x * (s.x / s.y);
    return st;
}
void main()
{
    vec2 pixelSize = 1. / RENDERSIZE;
    vec2 uv = gl_FragCoord.xy * pixelSize;
    if (PASSINDEX == 0) // Shadertoy Buffer A
    {
        vec4 noise = random4(uv + fract(vec2(42, 56) * TIME));
        // Get the gradients from the blurred image.
        vec2 d = pixelSize * gradientDistance;
        vec4 dx = IMG_NORM_PIXEL(bufferC, fract(uv + vec2(1, 0) * d)) - IMG_NORM_PIXEL(bufferC, fract(uv - vec2(1, 0) * d));
        vec4 dy = IMG_NORM_PIXEL(bufferC, fract(uv + vec2(0, 1) * d)) - IMG_NORM_PIXEL(bufferC, fract(uv - vec2(0, 1) * d));
        // Add some diffusive expansion.
        vec2 red = uv + vec2(dx.r, dy.r) * d * diffusionFactor;
        vec2 noise2 = noise.xy - 0.5;
        float newRed = IMG_NORM_PIXEL(bufferA, fract(red)).x + noise2.x * decayFactor - 0.002; // stochastic decay
        newRed -= (IMG_NORM_PIXEL(bufferC, fract(red + noise2 * pixelSize)).x -
                    IMG_NORM_PIXEL(bufferA, fract(red + noise2 * pixelSize)).x) * reactionDiffusionFactor; // reaction-diffusion
        if (FRAMEINDEX < 10) {
            gl_FragColor = noise;
        } else {
            gl_FragColor.r = clamp(newRed, 0., 1.);
        }
    }
    else if (PASSINDEX == 1) // Shadertoy Buffer B
    {
        // A limitation of ISF shaders is that the texture look-ups are
        // performed by macros like IMG_NORM_PIXEL, not functions like
        // texture(), and IMG_NORM_PIXEL expands to a function call (like
        // VVSAMPLER_2DBYNORM) that includes variables that are seemingly only
        // defined in main():
        //    https://github.com/search?q=owner%3AmrRay+VVSAMPLER_2DBYNORM&type=code
        // As a workaround, we define LYGIA’s Gaussian blur function as a macro
        // and completely expand it here.
        vec4 accumColor=vec4(0.); float kernelSizef = float(9); float accumWeight = 0.0; float k = gaussianKernelScale; for (int i = 0; i < 9; i++) { float x = -0.5 * ( kernelSizef -1.0) + float(i); float weight = (k / kernelSizef) * gaussian(x, kernelSizef * standardDeviationScale); vec4 tex = IMG_NORM_PIXEL(bufferA, fract(vec2(uv.x + (x * (pixelSize.x)), uv.y))); accumColor += weight * tex; accumWeight += weight; }
        gl_FragColor.rgb = accumColor.rgb / accumWeight;
        gl_FragColor.a = 1.;
    }
    else if (PASSINDEX == 2) // Shadertoy Buffer C
    {
        vec4 accumColor=vec4(0.); float kernelSizef = float(9); float accumWeight = 0.0; float k = gaussianKernelScale; for (int i = 0; i < 9; i++) { float x = -0.5 * ( kernelSizef -1.0) + float(i); float weight = (k / kernelSizef) * gaussian(x, kernelSizef * standardDeviationScale); vec4 tex = IMG_NORM_PIXEL(bufferB, fract(vec2(uv.x, uv.y + (x * (pixelSize.y))))); accumColor += weight * tex; accumWeight += weight; }
        gl_FragColor.rgb = accumColor.rgb / accumWeight;
        gl_FragColor.a = 1.;
    }
    else // Shadertoy Image
    {
        // Add the pixel gradients.
        vec2 d = pixelSize;
        vec4 dx = IMG_NORM_PIXEL(bufferA, uv + vec2(1, 0) * d) - IMG_NORM_PIXEL(bufferA, uv - vec2(1, 0) * d);
        vec4 dy = IMG_NORM_PIXEL(bufferA, uv + vec2(0, 1) * d) - IMG_NORM_PIXEL(bufferA, uv - vec2(0, 1) * d);
        // Recolor the red channel.
        gl_FragColor = vec4(IMG_NORM_PIXEL(bufferA, uv + vec2(dx.x, dy.x) * pixelSize * 8.).x) * vec4(0.7, 1.5, 2, 1) - vec4(0.3, 1, 1, 1);
        // Add the light map.
        vec2 lightSize = vec2(1. / inverseLightSize);
        // Use only the red gradient as displacement vector.
        vec2 displacement = vec2(dx.r, dy.r) * lightSize;
        lightSize = aspect(lightSize.yx, RENDERSIZE.yx).yx;
        float light = 0.;
        if (showLightWithMouse) {
            light += pow(
                max(1. - distance(0.5 + (uv - 0.5) * lightSize + displacement, 0.5 + (mouse.xy - 0.5) * lightSize), 0.),
                radiance
            );
        }
        if (showLightWithInputImage) {
            light += pow(
                distance(displacement, luminance(IMG_NORM_PIXEL(inputImage, uv)) * lightSize),
                radiance
            );
        }
        gl_FragColor = mix(
            gl_FragColor,
            vec4(8, 6, 2, 1),
            light * 0.75 * vec4(1. - IMG_NORM_PIXEL(bufferA, uv + vec2(dx.x, dy.x) * pixelSize * 8.).x)
        );
        gl_FragColor.a = 1.;
    }
}
'''


class IsfExpansiveReactionDiffusion(GlslEffect):
    name = 'isf_expansive_reaction_diffusion'
    label = 'Expansive reaction diffusion'
    description = 'Reaction-diffusion system, converted from <https://www.shadertoy.com/view/4dcGW2>'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'stylise'
    scale = 0.35
    params = effect_params({
        "gradient_distance": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 10.0,
            "default": 4.0,
            "x-ui": {
                "label": "Gradient distance",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "gradientDistance"
            }
        },
        "diffusion_factor": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 1.0,
            "x-ui": {
                "label": "Diffusion factor",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "diffusionFactor"
            }
        },
        "reaction_diffusion_factor": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.047,
            "x-ui": {
                "label": "Reaction-diffusion factor",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "reactionDiffusionFactor"
            }
        },
        "decay_factor": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0025,
            "x-ui": {
                "label": "Decay factor",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "decayFactor"
            }
        },
        "gaussian_kernel_scale": {
            "type": "number",
            "minimum": -10.0,
            "maximum": 10.0,
            "default": 1.44,
            "x-ui": {
                "label": "Gaussian kernel scale",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "gaussianKernelScale"
            }
        },
        "standard_deviation_scale": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 0.2913965934,
            "x-ui": {
                "label": "Standard deviation scale",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "standardDeviationScale"
            }
        },
        "show_light_with_input_image": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Show light with input image"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "showLightWithInputImage"
            }
        },
        "show_light_with_mouse": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Show light with mouse"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "showLightWithMouse"
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
        "inverse_light_size": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 0.25,
            "x-ui": {
                "label": "Light size",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "inverseLightSize"
            }
        },
        "radiance": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 4.0,
            "x-ui": {
                "label": "Radiance",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "radiance"
            }
        }
    })
