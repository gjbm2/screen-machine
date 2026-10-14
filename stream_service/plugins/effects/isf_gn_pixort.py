"""Gn  Pixort: Advanced Pixel Sorting for VDMX6

Source: https://github.com/Gnomalab/VDMX-ISF-Effects-Suite/blob/HEAD/ISF/Gn%20%20Pixort/Gn%20%20Pixort.fs
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
        "Gnomalab"
    ],
    "CREDIT": "Gnomalab",
    "DESCRIPTION": "Advanced Pixel Sorting for VDMX6",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0,
            "LABELS": [
                "Luma",
                "Saturación",
                "Brillo"
            ],
            "NAME": "mode",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2
            ]
        },
        {
            "DEFAULT": false,
            "LABEL": "Vertical (ON) / Horizontal (OFF)",
            "NAME": "direction",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 0.5,
            "LABEL": "Umbral",
            "MAX": 1,
            "MIN": 0,
            "NAME": "threshold",
            "TYPE": "float"
        },
        {
            "DEFAULT": false,
            "LABEL": "Invertir Selección",
            "NAME": "invertThreshold",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 0.2,
            "LABEL": "Largo de Estela",
            "MAX": 0.8,
            "MIN": 0,
            "NAME": "length",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1,
            "LABEL": "Mezcla",
            "MAX": 1,
            "MIN": 0,
            "NAME": "mixAmount",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2",
    "VSN": "1.0.0"
}
*/

// Funciones auxiliares para análisis de imagen
float getLuma(vec4 c) { return dot(c.rgb, vec3(0.299, 0.587, 0.114)); }
float getBr(vec4 c) { return (c.r + c.g + c.b) / 3.0; }
float getSat(vec4 c) {
    float maxVal = max(c.r, max(c.g, c.b));
    float minVal = min(c.r, min(c.g, c.b));
    return (maxVal == 0.0) ? 0.0 : (maxVal - minVal) / maxVal;
}

float getValue(vec4 c) {
    if (mode == 0) return getLuma(c);
    if (mode == 1) return getSat(c);
    return getBr(c);
}

void main() {
    vec2 uv = isf_FragNormCoord.xy;
    vec4 src = IMG_NORM_PIXEL(inputImage, uv);
    
    vec2 dirVec = direction ? vec2(0.0, 1.0) : vec2(1.0, 0.0);
    vec4 finalCol = src;
    
    // Decidir si el píxel es "activo" basado en el umbral e inversión
    float val = getValue(src);
    bool isActive = invertThreshold ? (val < threshold) : (val > threshold);

    if (isActive) {
        // Bucle de búsqueda (simula el desplazamiento de color)
        // 40 iteraciones dan un buen balance entre calidad y rendimiento
        for (int i = 1; i < 40; i++) {
            float f = float(i) / 40.0;
            vec2 lookupUV = uv - (dirVec * f * length);
            
            // Evitar que lea fuera de los bordes
            if (lookupUV.x < 0.0 || lookupUV.y < 0.0 || lookupUV.x > 1.0 || lookupUV.y > 1.0) break;

            vec4 checkCol = IMG_NORM_PIXEL(inputImage, lookupUV);
            float checkVal = getValue(checkCol);
            bool checkActive = invertThreshold ? (checkVal < threshold) : (checkVal > threshold);

            if (checkActive) {
                finalCol = checkCol;
            } else {
                break; // Se detiene cuando encuentra un píxel que no cumple el criterio
            }
        }
    }

    gl_FragColor = mix(src, finalCol, mixAmount);
}'''


class IsfGnPixort(GlslEffect):
    name = 'isf_gn_pixort'
    label = 'Gn  Pixort'
    description = 'Advanced Pixel Sorting for VDMX6'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.5
    params = effect_params({
        "mode": {
            "type": "integer",
            "enum": [
                0,
                1,
                2
            ],
            "default": 0,
            "x-ui": {
                "label": "Mode",
                "widget": "select",
                "labels": [
                    "Luma",
                    "Saturación",
                    "Brillo"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "mode"
            }
        },
        "direction": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Vertical (ON) / Horizontal (OFF)"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "direction"
            }
        },
        "threshold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Umbral",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "threshold"
            }
        },
        "invert_threshold": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Invertir Selección"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "invertThreshold"
            }
        },
        "length": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.8,
            "default": 0.2,
            "x-ui": {
                "label": "Largo de Estela",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "length"
            }
        },
        "mix_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "Mezcla",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mixAmount"
            }
        }
    })
