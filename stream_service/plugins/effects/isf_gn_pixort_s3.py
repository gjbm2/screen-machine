"""Gn Pixort S3: "Motor de pixel sorting vectorial avanzado con 8 modos de flujo (Radial, Espiral, Dipolo, etc.). Incluye control de polos magnéticos, detección de bordes Sobel...

Source: https://github.com/Gnomalab/VDMX-ISF-Effects-Suite/blob/HEAD/ISF/Gn%20Pixort%20S3/Gn%20Pixort%20S3.fs
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
    "DESCRIPTION": "\"Motor de pixel sorting vectorial avanzado con 8 modos de flujo (Radial, Espiral, Dipolo, etc.). Incluye control de polos magnéticos, detección de bordes Sobel para máscaras, aberración cromática y persistencia temporal.\"",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Modo de Flujo",
            "LABELS": [
                "Lineal",
                "Radial (Polo A)",
                "Espiral (Polo A)",
                "Concentrico (Polo A)",
                "Metafase-Atraccion",
                "Metafase-Repeler",
                "Dipolo-Atraccion",
                "Dipolo-Repeler"
            ],
            "NAME": "flowMode",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2,
                3,
                4,
                5,
                6,
                7
            ]
        },
        {
            "DEFAULT": [
                0.5,
                0.5
            ],
            "LABEL": "Polo A / Centro Flow",
            "NAME": "poleA",
            "TYPE": "point2D"
        },
        {
            "DEFAULT": [
                0.75,
                0.5
            ],
            "LABEL": "Polo B (Dipolo)",
            "NAME": "poleB",
            "TYPE": "point2D"
        },
        {
            "DEFAULT": false,
            "LABEL": "Restringir Ejes (Constraint)",
            "NAME": "constrainFlow",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 90,
            "LABEL": "Angulo / Rotacion",
            "MAX": 360,
            "MIN": 0,
            "NAME": "angle",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Velocidad Auto",
            "MAX": 2,
            "MIN": -2,
            "NAME": "flowSpeed",
            "TYPE": "float"
        },
        {
            "DEFAULT": false,
            "LABEL": "Invertir Direccion",
            "NAME": "invertFlow",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Umbral basado en:",
            "LABELS": [
                "Luminancia",
                "Matiz (Hue)",
                "Saturacion",
                "Ligereza",
                "Bordes (Sobel)",
                "Rojo",
                "Verde",
                "Azul",
                "Alfa"
            ],
            "NAME": "thresholdMode",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2,
                3,
                4,
                5,
                6,
                7,
                8
            ]
        },
        {
            "DEFAULT": 0.1,
            "LABEL": "Umbral Min",
            "MAX": 1,
            "MIN": 0,
            "NAME": "thresholdMin",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1,
            "LABEL": "Umbral Max",
            "MAX": 1,
            "MIN": 0,
            "NAME": "thresholdMax",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.05,
            "LABEL": "Suavizado Umbral",
            "MAX": 0.5,
            "MIN": 0,
            "NAME": "thresholdSmooth",
            "TYPE": "float"
        },
        {
            "DEFAULT": false,
            "LABEL": "Invertir Mascara",
            "NAME": "invertMask",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Sesgo de Ordenacion (Sort Bias):",
            "LABELS": [
                "Ninguno",
                "Suma RGB",
                "Suma RGBA",
                "Luminancia",
                "Matiz",
                "Saturacion",
                "Ligereza",
                "Crominancia I",
                "Crominancia Q"
            ],
            "NAME": "sortBiasMode",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2,
                3,
                4,
                5,
                6,
                7,
                8
            ]
        },
        {
            "DEFAULT": false,
            "LABEL": "Invertir Orden (Bias)",
            "NAME": "reverseSort",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 15,
            "LABEL": "Fuerza Desplazamiento",
            "MAX": 80,
            "MIN": 0,
            "NAME": "pixelStrength",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.95,
            "LABEL": "Persistencia",
            "MAX": 1,
            "MIN": 0,
            "NAME": "feedback",
            "TYPE": "float"
        },
        {
            "DEFAULT": 2,
            "LABEL": "Aberracion Cromatica",
            "MAX": 20,
            "MIN": 0,
            "NAME": "chromaAbr",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.1,
            "LABEL": "Glow (Sutil)",
            "MAX": 1,
            "MIN": 0,
            "NAME": "glow",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.05,
            "LABEL": "Dithering (Grano)",
            "MAX": 0.3,
            "MIN": 0,
            "NAME": "ditherAmount",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Bias Rojo",
            "MAX": 0.02,
            "MIN": -0.02,
            "NAME": "biasR",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Bias Verde",
            "MAX": 0.02,
            "MIN": -0.02,
            "NAME": "biasG",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Bias Azul",
            "MAX": 0.02,
            "MIN": -0.02,
            "NAME": "biasB",
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

float getNoise(vec2 p) {
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453123 + TIME);
}

vec3 rgb2hsv(vec3 c) {
    vec4 K = vec4(0.0, -1.0 / 3.0, 2.0 / 3.0, -1.0);
    vec4 p = mix(vec4(c.bg, K.wz), vec4(c.gb, K.xy), step(c.b, c.g));
    vec4 q = mix(vec4(p.xyw, c.r), vec4(c.r, p.yzx), step(p.x, c.r));
    float d = q.x - min(q.w, q.y);
    return vec3(abs(q.z + (q.w - q.y) / (6.0 * d + 1e-10)), d / (q.x + 1e-10), q.x);
}

void main() {
    vec2 uv = isf_FragNormCoord.xy;
    vec4 img = IMG_NORM_PIXEL(inputImage, uv);
    float aspect = RENDERSIZE.x / RENDERSIZE.y;

    // 1. OBTENER VALOR PARA LA MASCARA
    float val = 0.0;
    if (thresholdMode == 0) val = dot(img.rgb, vec3(0.299, 0.587, 0.114));
    else if (thresholdMode == 1) val = rgb2hsv(img.rgb).x;
    else if (thresholdMode == 2) val = rgb2hsv(img.rgb).y;
    else if (thresholdMode == 3) val = (max(img.r, max(img.g, img.b)) + min(img.r, min(img.g, img.b))) * 0.5;
    else if (thresholdMode == 4) {
        vec2 off = 1.0 / RENDERSIZE;
        float h = dot(IMG_NORM_PIXEL(inputImage, uv + vec2(-off.x, 0)).rgb, vec3(0.33)) - dot(IMG_NORM_PIXEL(inputImage, uv + vec2(off.x, 0)).rgb, vec3(0.33));
        float v = dot(IMG_NORM_PIXEL(inputImage, uv + vec2(0, -off.y)).rgb, vec3(0.33)) - dot(IMG_NORM_PIXEL(inputImage, uv + vec2(0, off.y)).rgb, vec3(0.33));
        val = length(vec2(h, v)) * 12.0;
    }
    else if (thresholdMode == 5) val = img.r;
    else if (thresholdMode == 6) val = img.g;
    else if (thresholdMode == 7) val = img.b;
    else if (thresholdMode == 8) val = img.a;

    float mask = smoothstep(thresholdMin - thresholdSmooth, thresholdMin, val) * (1.0 - smoothstep(thresholdMax, thresholdMax + thresholdSmooth, val));
    if (invertMask) mask = 1.0 - mask;

    // 2. SESGO DE ORDENACION (SORT BIAS)
    float sBias = 1.0;
    if (sortBiasMode == 1) sBias = (img.r + img.g + img.b) / 3.0;
    else if (sortBiasMode == 2) sBias = (img.r + img.g + img.b + img.a) / 4.0;
    else if (sortBiasMode == 3) sBias = dot(img.rgb, vec3(0.299, 0.587, 0.114));
    else if (sortBiasMode == 4) sBias = rgb2hsv(img.rgb).x;
    else if (sortBiasMode == 5) sBias = rgb2hsv(img.rgb).y;
    else if (sortBiasMode == 7) sBias = dot(img.rgb, vec3(0.596, -0.274, -0.322));
    else if (sortBiasMode == 8) sBias = dot(img.rgb, vec3(0.211, -0.523, 0.312));
    if (reverseSort) sBias = 1.0 - sBias;

    // 3. CALCULO DE VECTORES (FIX TOTAL)
    vec2 dir = vec2(0.0);
    vec2 loc = uv - poleA; 
    loc.x *= aspect; // Corrección visual para que el círculo sea REDONDO
    
    float rot = radians(angle + (TIME * flowSpeed * 60.0));

    if (flowMode == 0) { // Lineal
        dir = vec2(cos(rot), sin(rot));
    }
    else if (flowMode == 1) { // Radial
        dir = normalize(loc + 0.00001);
    }
    else if (flowMode == 2) { // Espiral
        vec2 tangential = vec2(-loc.y, loc.x);
        dir = normalize(tangential + loc * 0.5);
    }
    else if (flowMode == 3) { // Concentrico
        dir = normalize(vec2(-loc.y, loc.x));
    }
    else if (flowMode == 4 || flowMode == 5) { // Metafase
        dir = vec2(0.0, uv.y - poleA.y);
        if (flowMode == 4) dir.y *= -1.0;
        dir = normalize(dir + 0.00001);
    }
    else if (flowMode == 6 || flowMode == 7) { // Dipolo
        vec2 locB = uv - poleB; locB.x *= aspect;
        dir = normalize(loc + 0.00001) - normalize(locB + 0.00001);
        if (flowMode == 6) dir *= -1.0;
        dir = normalize(dir + 0.00001);
    }

    // MAPEAR DIRECCION DE VUELTA AL ESPACIO UV (Importante)
    dir.x /= aspect; 
    if (invertFlow) dir *= -1.0;

    if (constrainFlow) {
        if (abs(dir.x) > abs(dir.y)) dir = vec2(sign(dir.x), 0.0);
        else dir = vec2(0.0, sign(dir.y));
    }

    // 4. MUESTREO DE FEEDBACK
    vec2 move = (dir * pixelStrength * sBias) / RENDERSIZE;
    vec2 prevUV = uv - move;
    
    vec2 abr = (dir * chromaAbr) / RENDERSIZE;
    vec4 prevFrame;
    prevFrame.r = IMG_NORM_PIXEL(bufferVariable, clamp(prevUV - abr, 0.0, 1.0)).r;
    prevFrame.g = IMG_NORM_PIXEL(bufferVariable, clamp(prevUV, 0.0, 1.0)).g;
    prevFrame.b = IMG_NORM_PIXEL(bufferVariable, clamp(prevUV + abr, 0.0, 1.0)).b;
    prevFrame.a = 1.0;

    prevFrame.rgb += vec3(biasR, biasG, biasB);
    prevFrame.rgb += (getNoise(uv * 100.0) - 0.5) * ditherAmount;
    prevFrame = clamp(prevFrame, 0.0, 1.0);

    // 5. COMPOSICION
    vec4 sorting = mix(img, prevFrame, feedback);
    sorting.rgb += (prevFrame.rgb * prevFrame.rgb) * (glow * 0.1) * mask;

    vec4 finalColor = mix(img, sorting, mask);
    if (clearBuffer) finalColor = img;

    gl_FragColor = finalColor;
}'''


class IsfGnPixortS3(GlslEffect):
    name = 'isf_gn_pixort_s3'
    label = 'Gn Pixort S3'
    description = '"Motor de pixel sorting vectorial avanzado con 8 modos de flujo (Radial, Espiral, Dipolo, etc.). Incluye control de polos magnéticos, detección de bordes Sobel...'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.5
    events = ('clearBuffer',)
    params = effect_params({
        "flow_mode": {
            "type": "integer",
            "enum": [
                0,
                1,
                2,
                3,
                4,
                5,
                6,
                7
            ],
            "default": 0,
            "x-ui": {
                "label": "Modo de Flujo",
                "widget": "select",
                "labels": [
                    "Lineal",
                    "Radial (Polo A)",
                    "Espiral (Polo A)",
                    "Concentrico (Polo A)",
                    "Metafase-Atraccion",
                    "Metafase-Repeler",
                    "Dipolo-Atraccion",
                    "Dipolo-Repeler"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "flowMode"
            }
        },
        "pole_a_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Polo A / Centro Flow x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "poleA",
                "c": 0
            }
        },
        "pole_a_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Polo A / Centro Flow y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "poleA",
                "c": 1
            }
        },
        "pole_b_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.75,
            "x-ui": {
                "label": "Polo B (Dipolo) x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "poleB",
                "c": 0
            }
        },
        "pole_b_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Polo B (Dipolo) y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "poleB",
                "c": 1
            }
        },
        "constrain_flow": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Restringir Ejes (Constraint)"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "constrainFlow"
            }
        },
        "angle": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 360.0,
            "default": 90.0,
            "x-ui": {
                "label": "Angulo / Rotacion",
                "step": 1.0,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "angle"
            }
        },
        "flow_speed": {
            "type": "number",
            "minimum": -2.0,
            "maximum": 2.0,
            "default": 0.0,
            "x-ui": {
                "label": "Velocidad Auto",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flowSpeed"
            }
        },
        "invert_flow": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Invertir Direccion"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "invertFlow"
            }
        },
        "threshold_mode": {
            "type": "integer",
            "enum": [
                0,
                1,
                2,
                3,
                4,
                5,
                6,
                7,
                8
            ],
            "default": 0,
            "x-ui": {
                "label": "Umbral basado en:",
                "widget": "select",
                "labels": [
                    "Luminancia",
                    "Matiz (Hue)",
                    "Saturacion",
                    "Ligereza",
                    "Bordes (Sobel)",
                    "Rojo",
                    "Verde",
                    "Azul",
                    "Alfa"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "thresholdMode"
            }
        },
        "threshold_min": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.1,
            "x-ui": {
                "label": "Umbral Min",
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
            "default": 1.0,
            "x-ui": {
                "label": "Umbral Max",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "thresholdMax"
            }
        },
        "threshold_smooth": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.5,
            "default": 0.05,
            "x-ui": {
                "label": "Suavizado Umbral",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "thresholdSmooth"
            }
        },
        "invert_mask": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Invertir Mascara"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "invertMask"
            }
        },
        "sort_bias_mode": {
            "type": "integer",
            "enum": [
                0,
                1,
                2,
                3,
                4,
                5,
                6,
                7,
                8
            ],
            "default": 0,
            "x-ui": {
                "label": "Sesgo de Ordenacion (Sort Bias):",
                "widget": "select",
                "labels": [
                    "Ninguno",
                    "Suma RGB",
                    "Suma RGBA",
                    "Luminancia",
                    "Matiz",
                    "Saturacion",
                    "Ligereza",
                    "Crominancia I",
                    "Crominancia Q"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "sortBiasMode"
            }
        },
        "reverse_sort": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Invertir Orden (Bias)"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "reverseSort"
            }
        },
        "pixel_strength": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 80.0,
            "default": 15.0,
            "x-ui": {
                "label": "Fuerza Desplazamiento",
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
                "label": "Persistencia",
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
            "maximum": 20.0,
            "default": 2.0,
            "x-ui": {
                "label": "Aberracion Cromatica",
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
            "default": 0.1,
            "x-ui": {
                "label": "Glow (Sutil)",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "glow"
            }
        },
        "dither_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.3,
            "default": 0.05,
            "x-ui": {
                "label": "Dithering (Grano)",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "ditherAmount"
            }
        },
        "bias_r": {
            "type": "number",
            "minimum": -0.02,
            "maximum": 0.02,
            "default": 0.0,
            "x-ui": {
                "label": "Bias Rojo",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "biasR"
            }
        },
        "bias_g": {
            "type": "number",
            "minimum": -0.02,
            "maximum": 0.02,
            "default": 0.0,
            "x-ui": {
                "label": "Bias Verde",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "biasG"
            }
        },
        "bias_b": {
            "type": "number",
            "minimum": -0.02,
            "maximum": 0.02,
            "default": 0.0,
            "x-ui": {
                "label": "Bias Azul",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "biasB"
            }
        }
    })
