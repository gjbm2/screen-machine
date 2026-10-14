"""Gn XORmear: "Procesador de estelas digitales con corrupción bitwise XOR. Permite aislar rangos de luminancia específicos y alternar entre texturas de barrido rugosas o...

Source: https://github.com/Gnomalab/VDMX-ISF-Effects-Suite/blob/HEAD/ISF/Gn%20XORmear/Gn%20XORmear.fs
Author: Gnomalab
License: MIT
license_class: permissive
Imported verbatim from GLSL (Gnomalab/VDMX-ISF-Effects-Suite); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Gnomalab/VDMX-ISF-Effects-Suite LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Glitch",
        "Distortion",
        "Gnomalab"
    ],
    "CREDIT": "Gnomalab",
    "DESCRIPTION": "\"Procesador de estelas digitales con corrupción bitwise XOR. Permite aislar rangos de luminancia específicos y alternar entre texturas de barrido rugosas o sólidas mediante su ruteo de doble etapa.\"",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Umbral MIN",
            "MAX": 1,
            "MIN": 0,
            "NAME": "threshold_min",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1,
            "LABEL": "Umbral MAX",
            "MAX": 1,
            "MIN": 0,
            "NAME": "threshold_max",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.1,
            "LABEL": "Suavizado (Softness)",
            "MAX": 0.5,
            "MIN": 0,
            "NAME": "softness",
            "TYPE": "float"
        },
        {
            "DEFAULT": false,
            "LABEL": "Invertir Mascara",
            "NAME": "mask_invert",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 0,
            "LABEL": "ORDEN",
            "LABELS": [
                "1: XOR > Smear",
                "2: Smear > XOR"
            ],
            "NAME": "processing_order",
            "TYPE": "long",
            "VALUES": [
                0,
                1
            ]
        },
        {
            "DEFAULT": 10,
            "LABEL": "XOR Amount",
            "MAX": 255,
            "MIN": 0,
            "NAME": "xor_val",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.4,
            "LABEL": "Smear Length",
            "MAX": 1,
            "MIN": 0,
            "NAME": "smear_len",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Smear Angle",
            "MAX": 1,
            "MIN": 0,
            "NAME": "smear_angle",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2",
    "VSN": "1.0.0"
}
*/

// --- FUNCION XOR MANUAL ---
float manual_xor(float a, float b) {
    float result = 0.0;
    for(int i = 0; i < 8; ++i) {
        float p = pow(2.0, float(i));
        float a_bit = mod(floor(a / p), 2.0);
        float b_bit = mod(floor(b / p), 2.0);
        if (abs(a_bit - b_bit) > 0.5) result += p;
    }
    return result;
}

vec4 applyXOR(vec4 col, float xVal) {
    float r = manual_xor(floor(col.r * 255.0), xVal) / 255.0;
    float g = manual_xor(floor(col.g * 255.0), xVal) / 255.0;
    float b = manual_xor(floor(col.b * 255.0), xVal) / 255.0;
    return vec4(r, g, b, col.a);
}

float getLuma(vec4 col) {
    return dot(col.rgb, vec3(0.299, 0.587, 0.114));
}

void main() {
    vec2 uv = isf_FragNormCoord;
    vec4 baseCol = IMG_NORM_PIXEL(inputImage, uv);
    
    // 1. Calcular Mascara de Banda (Min/Max)
    float luma = getLuma(baseCol);
    
    // Suavizado en el minimo y en el maximo
    float mask_low = smoothstep(threshold_min - softness, threshold_min + softness, luma);
    float mask_high = 1.0 - smoothstep(threshold_max - softness, threshold_max + softness, luma);
    float mask = mask_low * mask_high;
    
    if (mask_invert) mask = 1.0 - mask;

    vec4 finalCol;
    vec2 dir = vec2(cos(smear_angle * 6.2831), sin(smear_angle * 6.2831));
    float samples = 16.0; 
    float distMult = 0.8; 

    if (processing_order == 0) {
        // --- MODO 1: XOR > SMEAR ---
        vec4 acc = vec4(0.0);
        for(float i = 0.0; i < 16.0; i++) {
            vec2 offset = dir * (i / samples) * smear_len * distMult;
            vec4 pix = IMG_NORM_PIXEL(inputImage, fract(uv - offset));
            acc += applyXOR(pix, xor_val);
        }
        finalCol = mix(baseCol, acc / samples, mask);

    } else {
        // --- MODO 2: SMEAR > XOR ---
        vec4 acc = vec4(0.0);
        for(float i = 0.0; i < 16.0; i++) {
            vec2 offset = dir * (i / samples) * smear_len * distMult;
            acc += IMG_NORM_PIXEL(inputImage, fract(uv - offset));
        }
        vec4 smearClean = acc / samples;
        vec4 xorResult = applyXOR(smearClean, xor_val);
        finalCol = mix(baseCol, xorResult, mask);
    }

    gl_FragColor = finalCol;
}'''


class IsfGnXormear(GlslEffect):
    name = 'isf_gn_xormear'
    label = 'Gn XORmear'
    description = '"Procesador de estelas digitales con corrupción bitwise XOR. Permite aislar rangos de luminancia específicos y alternar entre texturas de barrido rugosas o...'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.35
    params = effect_params({
        "threshold_min": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Umbral MIN",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "threshold_min"
            }
        },
        "threshold_max": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "Umbral MAX",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "threshold_max"
            }
        },
        "softness": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.5,
            "default": 0.1,
            "x-ui": {
                "label": "Suavizado (Softness)",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "softness"
            }
        },
        "mask_invert": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Invertir Mascara"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mask_invert"
            }
        },
        "processing_order": {
            "type": "integer",
            "enum": [
                0,
                1
            ],
            "default": 0,
            "x-ui": {
                "label": "ORDEN",
                "widget": "select",
                "labels": [
                    "1: XOR > Smear",
                    "2: Smear > XOR"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "processing_order"
            }
        },
        "xor_val": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 255.0,
            "default": 10.0,
            "x-ui": {
                "label": "XOR Amount",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "xor_val"
            }
        },
        "smear_len": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.4,
            "x-ui": {
                "label": "Smear Length",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "smear_len"
            }
        },
        "smear_angle": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Smear Angle",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "smear_angle"
            }
        }
    })
