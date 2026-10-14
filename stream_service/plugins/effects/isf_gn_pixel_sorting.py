"""Gn Pixel Sorting: Pixel sorter persistente con modulación rítmica de velocidad. Incluye controles de cadencia (pulse), desvanecimiento de color (desaturación/oscurecimiento) y...

Source: https://github.com/Gnomalab/VDMX-ISF-Effects-Suite/blob/HEAD/ISF/Gn%20Pixel%20Sorting/Gn%20Pixel%20Sorting.fs
Author: Gnomalab
License: MIT
license_class: permissive
Imported verbatim from GLSL (Gnomalab/VDMX-ISF-Effects-Suite); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Gnomalab/VDMX-ISF-Effects-Suite LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Gnomalab",
        "Glitch"
    ],
    "CREDIT": "Gnomalab",
    "DESCRIPTION": "Pixel sorter persistente con modulación rítmica de velocidad. Incluye controles de cadencia (pulse), desvanecimiento de color (desaturación/oscurecimiento) y selección por rangos de luminancia.\"",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": false,
            "LABEL": "Modo Horizontal",
            "NAME": "horizontalMode",
            "TYPE": "bool"
        },
        {
            "DEFAULT": false,
            "LABEL": "Invertir Dirección",
            "NAME": "invertDirection",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 0.25,
            "LABEL": "Umbral Mínimo",
            "MAX": 1,
            "MIN": 0,
            "NAME": "thresholdMin",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.8,
            "LABEL": "Umbral Máximo",
            "MAX": 1,
            "MIN": 0,
            "NAME": "thresholdMax",
            "TYPE": "float"
        },
        {
            "DEFAULT": false,
            "LABEL": "Invertir Selección FX",
            "NAME": "invertSelection",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 0.005,
            "LABEL": "Velocidad Base",
            "MAX": 0.05,
            "MIN": 0,
            "NAME": "meltSpeed",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Ritmo Cadencia",
            "MAX": 10,
            "MIN": 0,
            "NAME": "cadenceRate",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.01,
            "LABEL": "Desvanecer (Fade)",
            "MAX": 0.2,
            "MIN": 0,
            "NAME": "colorFade",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.98,
            "LABEL": "Persistencia (Feedback)",
            "MAX": 1,
            "MIN": 0,
            "NAME": "feedback",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2",
    "NAME": "Gnomalab_PixelSorter_V3",
    "PASSES": [
        {
            "PERSISTENT": true,
            "TARGET": "bufferAccum"
        }
    ],
    "VSN": "1.0.0"
}
*/

void main() {
    vec2 uv = isf_FragNormCoord;
    vec4 img = IMG_NORM_PIXEL(inputImage, uv);
    
    // Luminancia para el umbral
    float bright = dot(img.rgb, vec3(0.299, 0.587, 0.114));
    
    // Lógica de Selección
    bool isInRange = (bright >= thresholdMin && bright <= thresholdMax);
    if (invertSelection) isInRange = !isInRange;

    // Cálculo de movimiento y cadencia
    float pulse = sin(TIME * cadenceRate * 6.28318);
    float dynamicSpeed = meltSpeed * (1.0 + pulse);
    float finalSpeed = invertDirection ? -dynamicSpeed : dynamicSpeed;
    vec2 offset = horizontalMode ? vec2(finalSpeed, 0.0) : vec2(0.0, finalSpeed);

    if (isInRange) {
        // Obtenemos el color que viene de la dirección del movimiento
        vec4 prev = IMG_NORM_PIXEL(bufferAccum, uv + offset);
        
        // --- NUEVA LÓGICA DE FADE (MÁS AGRESIVA) ---
        // 1. Desaturamos
        float gray = dot(prev.rgb, vec3(0.3, 0.59, 0.11));
        prev.rgb = mix(prev.rgb, vec3(gray), colorFade * 2.0);
        
        // 2. Oscurecemos (para que la estela desaparezca visualmente)
        prev.rgb *= (1.0 - colorFade);
        
        // Mezclamos con la imagen actual según la persistencia
        gl_FragColor = mix(img, prev, feedback);
    } else {
        gl_FragColor = img;
    }
}'''


class IsfGnPixelSorting(GlslEffect):
    name = 'isf_gn_pixel_sorting'
    label = 'Gn Pixel Sorting'
    description = 'Pixel sorter persistente con modulación rítmica de velocidad. Incluye controles de cadencia (pulse), desvanecimiento de color (desaturación/oscurecimiento) y...'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.7
    params = effect_params({
        "horizontal_mode": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Modo Horizontal"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "horizontalMode"
            }
        },
        "invert_direction": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Invertir Dirección"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "invertDirection"
            }
        },
        "threshold_min": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.25,
            "x-ui": {
                "label": "Umbral Mínimo",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "thresholdMin"
            }
        },
        "threshold_max": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.8,
            "x-ui": {
                "label": "Umbral Máximo",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "thresholdMax"
            }
        },
        "invert_selection": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Invertir Selección FX"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "invertSelection"
            }
        },
        "melt_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.05,
            "default": 0.005,
            "x-ui": {
                "label": "Velocidad Base",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "meltSpeed"
            }
        },
        "cadence_rate": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 0.0,
            "x-ui": {
                "label": "Ritmo Cadencia",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "cadenceRate"
            }
        },
        "color_fade": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.2,
            "default": 0.01,
            "x-ui": {
                "label": "Desvanecer (Fade)",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "colorFade"
            }
        },
        "feedback": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.98,
            "x-ui": {
                "label": "Persistencia (Feedback)",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "feedback"
            }
        }
    })
