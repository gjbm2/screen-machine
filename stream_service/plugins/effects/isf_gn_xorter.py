"""Gn XORter: "Procesador híbrido de destrucción binaria y desplazamiento. Combina un motor XOR Bitwise con Pixel Sorting persistente. Permite alternar el orden de la cadena...

Source: https://github.com/Gnomalab/VDMX-ISF-Effects-Suite/blob/HEAD/ISF/Gn%20XORter/Gn%20XORter.fs
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
        "Gnomalab",
        "Destruction"
    ],
    "CREDIT": "Gnomalab",
    "DESCRIPTION": "\"Procesador híbrido de destrucción binaria y desplazamiento. Combina un motor XOR Bitwise con Pixel Sorting persistente. Permite alternar el orden de la cadena de proceso, generar aberración cromática rítmica y controlar la persistencia del error en el buffer.\"",
    "INPUTS": [
        {
            "LABEL": "Source Image",
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": false,
            "LABEL": "Invertir Orden (Sort -> XOR)",
            "NAME": "swapOrder",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 1,
            "LABEL": "Intensidad Global",
            "MAX": 1,
            "MIN": 0,
            "NAME": "masterIntensity",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.5,
            "LABEL": "XOR Threshold",
            "MAX": 1,
            "MIN": 0,
            "NAME": "xorThreshold",
            "TYPE": "float"
        },
        {
            "DEFAULT": 2.11,
            "LABEL": "XOR Bit Depth",
            "MAX": 8,
            "MIN": 1,
            "NAME": "xorBitDepth",
            "TYPE": "float"
        },
        {
            "DEFAULT": 59.39,
            "LABEL": "XOR Block Size",
            "MAX": 200,
            "MIN": 1,
            "NAME": "xorBlockSize",
            "TYPE": "float"
        },
        {
            "DEFAULT": 128,
            "LABEL": "XOR Rojo",
            "MAX": 255,
            "MIN": 0,
            "NAME": "xorRed",
            "TYPE": "float"
        },
        {
            "DEFAULT": 64,
            "LABEL": "XOR Verde",
            "MAX": 255,
            "MIN": 0,
            "NAME": "xorGreen",
            "TYPE": "float"
        },
        {
            "DEFAULT": 32,
            "LABEL": "XOR Azul",
            "MAX": 255,
            "MIN": 0,
            "NAME": "xorBlue",
            "TYPE": "float"
        },
        {
            "DEFAULT": 90,
            "LABEL": "Angulo Sort",
            "MAX": 360,
            "MIN": 0,
            "NAME": "angle",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.3,
            "LABEL": "Sort Hue Min",
            "MAX": 1,
            "MIN": 0,
            "NAME": "sortThresholdMin",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.8,
            "LABEL": "Sort Hue Max",
            "MAX": 1,
            "MIN": 0,
            "NAME": "sortThresholdMax",
            "TYPE": "float"
        },
        {
            "DEFAULT": 46.8,
            "LABEL": "Sort Strength",
            "MAX": 100,
            "MIN": 0,
            "NAME": "pixelStrength",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.95,
            "LABEL": "Sort Persistence",
            "MAX": 1,
            "MIN": 0,
            "NAME": "feedback",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1.53,
            "LABEL": "Sort Chroma Abr.",
            "MAX": 10,
            "MIN": 0,
            "NAME": "chromaAbr",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.17,
            "LABEL": "Sort Glow",
            "MAX": 1,
            "MIN": 0,
            "NAME": "glow",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.3,
            "LABEL": "Dithering",
            "MAX": 1,
            "MIN": 0,
            "NAME": "dither",
            "TYPE": "float"
        },
        {
            "LABEL": "Limpiar Buffer",
            "NAME": "clearBuffer",
            "TYPE": "event"
        }
    ],
    "ISFVSN": "2",
    "PASSES": [
        {
            "FLOAT": true,
            "PERSISTENT": true,
            "TARGET": "bufferVariable"
        }
    ],
    "VSN": "1.0.0"
}
*/

float xor_op(float a, float b) {
    float res = 0.0; float fac = 1.0;
    for (int i = 0; i < 8; i++) {
        bool ap = mod(a, 2.0) >= 1.0; bool bp = mod(b, 2.0) >= 1.0;
        if (ap != bp) res += fac;
        a = floor(a / 2.0); b = floor(b / 2.0); fac *= 2.0;
    }
    return res;
}

vec3 rgb2hsv(vec3 c) {
    vec4 K = vec4(0.0, -1.0 / 3.0, 2.0 / 3.0, -1.0);
    vec4 p = mix(vec4(c.bg, K.wz), vec4(c.gb, K.xy), step(c.b, c.g));
    vec4 q = mix(vec4(p.xyw, c.r), vec4(c.r, p.yzx), step(p.x, c.r));
    float d = q.x - min(q.w, q.y);
    return vec3(abs(q.z + (q.w - q.y) / (6.0 * d + 1e-10)), d / (q.x + 1e-10), q.x);
}

// Función modular para XOR
vec3 applyXor(vec3 colorIn, vec2 uv, vec2 res) {
    vec2 xorUV = (xorBlockSize > 1.0) ? (floor(uv * res / xorBlockSize) * xorBlockSize) / res : uv;
    vec4 xorSample = IMG_NORM_PIXEL(inputImage, xorUV);
    float luma = dot(colorIn, vec3(0.299, 0.587, 0.114));
    float xorMask = smoothstep(xorThreshold - 0.1, xorThreshold + 0.1, luma);
    float bLevels = pow(2.0, xorBitDepth);
    vec3 rgbBits = floor(xorSample.rgb * bLevels) * (255.0 / bLevels);
    vec3 xorRes;
    xorRes.r = xor_op(rgbBits.r, xorRed);
    xorRes.g = xor_op(rgbBits.g, xorGreen);
    xorRes.b = xor_op(rgbBits.b, xorBlue);
    return mix(colorIn, xorRes / 255.0, xorMask);
}

void main() {
    vec2 uv = isf_FragNormCoord.xy;
    vec2 res = RENDERSIZE.xy;
    vec4 src = IMG_NORM_PIXEL(inputImage, uv);
    vec3 finalEffect;

    if (!swapOrder) {
        // --- ORDEN: XOR -> SORT ---
        vec3 stage1 = applyXor(src.rgb, uv, res);
        
        vec3 hsv = rgb2hsv(stage1);
        float sortMask = smoothstep(sortThresholdMin - 0.05, sortThresholdMin, hsv.x) * (1.0 - smoothstep(sortThresholdMax, sortThresholdMax + 0.05, hsv.x));
        
        vec2 dir = vec2(cos(radians(angle)), sin(radians(angle)));
        vec2 move = (dir * pixelStrength) / res;
        vec2 abr = (dir * chromaAbr) / res;
        
        vec4 prev;
        prev.r = IMG_NORM_PIXEL(bufferVariable, clamp(uv - move - abr, 0.0, 1.0)).r;
        prev.g = IMG_NORM_PIXEL(bufferVariable, clamp(uv - move, 0.0, 1.0)).g;
        prev.b = IMG_NORM_PIXEL(bufferVariable, clamp(uv - move + abr, 0.0, 1.0)).b;
        prev.a = 1.0;
        
        prev.rgb += (fract(sin(dot(uv, vec2(12.9898, 78.233))) * 43758.5453) - 0.5) * dither * 0.1;
        vec3 combined = mix(stage1, prev.rgb, feedback);
        combined += (prev.rgb * prev.rgb) * glow * 0.2;
        finalEffect = mix(stage1, combined, sortMask);

    } else {
        // --- ORDEN: SORT -> XOR ---
        vec3 sortVal = rgb2hsv(src.rgb);
        float sortMask = smoothstep(sortThresholdMin - 0.05, sortThresholdMin, sortVal.x) * (1.0 - smoothstep(sortThresholdMax, sortThresholdMax + 0.05, sortVal.x));
        
        vec2 dir = vec2(cos(radians(angle)), sin(radians(angle)));
        vec2 move = (dir * pixelStrength) / res;
        vec2 abr = (dir * chromaAbr) / res;
        
        vec4 prev;
        prev.r = IMG_NORM_PIXEL(bufferVariable, clamp(uv - move - abr, 0.0, 1.0)).r;
        prev.g = IMG_NORM_PIXEL(bufferVariable, clamp(uv - move, 0.0, 1.0)).g;
        prev.b = IMG_NORM_PIXEL(bufferVariable, clamp(uv - move + abr, 0.0, 1.0)).b;
        prev.a = 1.0;
        
        prev.rgb += (fract(sin(dot(uv, vec2(12.9898, 78.233))) * 43758.5453) - 0.5) * dither * 0.1;
        vec3 combinedSort = mix(src.rgb, prev.rgb, feedback);
        combinedSort += (prev.rgb * prev.rgb) * glow * 0.2;
        vec3 stage1 = mix(src.rgb, combinedSort, sortMask);
        
        finalEffect = applyXor(stage1, uv, res);
    }

    vec4 finalColor = vec4(mix(src.rgb, finalEffect, masterIntensity), src.a);
    if (clearBuffer) finalColor = src;
    gl_FragColor = finalColor;
}'''


class IsfGnXorter(GlslEffect):
    name = 'isf_gn_xorter'
    label = 'Gn XORter'
    description = '"Procesador híbrido de destrucción binaria y desplazamiento. Combina un motor XOR Bitwise con Pixel Sorting persistente. Permite alternar el orden de la cadena...'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.45
    events = ('clearBuffer',)
    params = effect_params({
        "swap_order": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Invertir Orden (Sort -> XOR)"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "swapOrder"
            }
        },
        "master_intensity": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "Intensidad Global",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "masterIntensity"
            }
        },
        "xor_threshold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "XOR Threshold",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "xorThreshold"
            }
        },
        "xor_bit_depth": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 8.0,
            "default": 2.11,
            "x-ui": {
                "label": "XOR Bit Depth",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "xorBitDepth"
            }
        },
        "xor_block_size": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 200.0,
            "default": 59.39,
            "x-ui": {
                "label": "XOR Block Size",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "xorBlockSize"
            }
        },
        "xor_red": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 255.0,
            "default": 128.0,
            "x-ui": {
                "label": "XOR Rojo",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "xorRed"
            }
        },
        "xor_green": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 255.0,
            "default": 64.0,
            "x-ui": {
                "label": "XOR Verde",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "xorGreen"
            }
        },
        "xor_blue": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 255.0,
            "default": 32.0,
            "x-ui": {
                "label": "XOR Azul",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "xorBlue"
            }
        },
        "angle": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 360.0,
            "default": 90.0,
            "x-ui": {
                "label": "Angulo Sort",
                "step": 1.0,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "angle"
            }
        },
        "sort_threshold_min": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.3,
            "x-ui": {
                "label": "Sort Hue Min",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "sortThresholdMin"
            }
        },
        "sort_threshold_max": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.8,
            "x-ui": {
                "label": "Sort Hue Max",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "sortThresholdMax"
            }
        },
        "pixel_strength": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
            "default": 46.8,
            "x-ui": {
                "label": "Sort Strength",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "pixelStrength"
            }
        },
        "feedback": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.95,
            "x-ui": {
                "label": "Sort Persistence",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "feedback"
            }
        },
        "chroma_abr": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.53,
            "x-ui": {
                "label": "Sort Chroma Abr.",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "chromaAbr"
            }
        },
        "glow": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.17,
            "x-ui": {
                "label": "Sort Glow",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "glow"
            }
        },
        "dither": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.3,
            "x-ui": {
                "label": "Dithering",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "dither"
            }
        }
    })
