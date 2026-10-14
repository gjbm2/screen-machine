"""Gn Slit scan: "Motor de Slit-Scan avanzado con 4 direcciones de escaneo, deformación por mapas (gradientes/capas externas), separación RGB temporal y feedback persistente...

Source: https://github.com/Gnomalab/VDMX-ISF-Effects-Suite/blob/HEAD/ISF/Gn%20Slit-scan/Gn%20Slit-scan.fs
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
        "Time"
    ],
    "CREDIT": "Gnomalab",
    "DESCRIPTION": "\"Motor de Slit-Scan avanzado con 4 direcciones de escaneo, deformación por mapas (gradientes/capas externas), separación RGB temporal y feedback persistente con desplazamiento de color.\"",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 1,
            "LABEL": "Map Source",
            "LABELS": [
                "Horizontal Gradient",
                "Vertical Gradient",
                "Circular Gradient",
                "External Layer"
            ],
            "NAME": "mapMode",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2,
                3
            ]
        },
        {
            "LABEL": "External Layer Map",
            "NAME": "mapImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0.2,
            "LABEL": "Map Influence",
            "MAX": 2,
            "MIN": -1,
            "NAME": "mapInfluence",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.5,
            "LABEL": "Map Curve Smoothing",
            "MAX": 3,
            "MIN": 0,
            "NAME": "mapSmoothing",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.005,
            "LABEL": "Map Luma Blur",
            "MAX": 0.02,
            "MIN": 0,
            "NAME": "mapBlur",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Map Phase / Offset",
            "MAX": 1,
            "MIN": 0,
            "NAME": "mapPhase",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1,
            "LABEL": "Scan Direction",
            "LABELS": [
                "Moves Left",
                "Moves Right",
                "Moves Up",
                "Moves Down"
            ],
            "NAME": "scanDir",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2,
                3
            ]
        },
        {
            "DEFAULT": 0.5,
            "LABEL": "Base Slit Position",
            "MAX": 1,
            "MIN": 0,
            "NAME": "slitPos",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.002,
            "LABEL": "Scan Speed",
            "MAX": 0.01,
            "MIN": 0,
            "NAME": "speed",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.002,
            "LABEL": "RGB Split (Temporal)",
            "MAX": 0.02,
            "MIN": 0,
            "NAME": "rgbSplit",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Feedback (Ghosting)",
            "MAX": 0.95,
            "MIN": 0,
            "NAME": "feedbackAmount",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Feedback Hue Shift",
            "MAX": 5,
            "MIN": -5,
            "NAME": "hueShift",
            "TYPE": "float"
        },
        {
            "DEFAULT": false,
            "LABEL": "CLEAN BUFFER",
            "NAME": "cleanBuffer",
            "TYPE": "bool"
        }
    ],
    "ISFVSN": "2",
    "NAME": "Slit-Scan Gnomalab Master Clean",
    "PASSES": [
        {
            "PERSISTENT": true,
            "TARGET": "slitBuffer"
        },
        {
        }
    ],
    "VSN": "1.0.0"
}
*/

vec3 rgb2hsv(vec3 c) {
    vec4 K = vec4(0.0, -1.0 / 3.0, 2.0 / 3.0, -1.0);
    vec4 p = mix(vec4(c.bg, K.wz), vec4(c.gb, K.xy), step(c.b, c.g));
    vec4 q = mix(vec4(p.xyw, c.r), vec4(c.r, p.yzx), step(p.x, c.r));
    float d = q.x - min(q.w, q.y);
    return vec3(abs(q.z + (q.w - q.y) / (6.0 * d + 1.0e-10)), d / (q.x + 1.0e-10), q.x);
}

vec3 hsv2rgb(vec3 c) {
    vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
    vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
    return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

float getLuma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

float sampleMap(vec2 uv) {
    float raw = 0.0;
    if (mapMode == 0) raw = uv.x + mapPhase;
    else if (mapMode == 1) raw = uv.y + mapPhase;
    else if (mapMode == 2) raw = distance(uv, vec2(0.5)) * 2.0 + mapPhase;
    else if (mapMode == 3) raw = getLuma(IMG_NORM_PIXEL(mapImage, uv).rgb);
    return mix(fract(raw), 0.5 + 0.5 * cos(raw * 6.283185), mapSmoothing);
}

void main() {
    vec2 uv = isf_FragNormCoord;

    if (PASSINDEX == 0) {
        if (cleanBuffer) {
            gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);
        } else {
            float mapVal = sampleMap(uv);
            if (mapBlur > 0.0) {
                mapVal += sampleMap(uv + vec2(mapBlur, 0.0));
                mapVal += sampleMap(uv - vec2(mapBlur, 0.0));
                mapVal += sampleMap(uv + vec2(0.0, mapBlur));
                mapVal += sampleMap(uv - vec2(0.0, mapBlur));
                mapVal /= 5.0;
            }

            float defSlit = clamp(slitPos + (mapVal - 0.5) * mapInfluence, 0.0, 1.0);
            vec4 finalCol = vec4(0.0);
            
            // Calculamos velocidades por canal
            float sR = speed;
            float sG = speed + rgbSplit * 0.5;
            float sB = speed + rgbSplit;

            // --- LÓGICA DE PROCESAMIENTO POR CANAL ---
            // Canal R
            bool isNewR;
            vec2 offR;
            if (scanDir == 0) { isNewR = uv.x > 1.0-sR; offR = vec2(sR, 0); }
            else if (scanDir == 1) { isNewR = uv.x < sR; offR = vec2(-sR, 0); }
            else if (scanDir == 2) { isNewR = uv.y < sR; offR = vec2(0, -sR); } // Inyecta abajo, sube
            else { isNewR = uv.y > 1.0-sR; offR = vec2(0, sR); } // Inyecta arriba, baja

            if (isNewR) {
                finalCol.r = mix(IMG_NORM_PIXEL(inputImage, (scanDir<2) ? vec2(defSlit, uv.y) : vec2(uv.x, defSlit)).r, IMG_NORM_PIXEL(slitBuffer, uv).r, feedbackAmount);
            } else {
                finalCol.r = IMG_NORM_PIXEL(slitBuffer, uv + offR).r;
            }

            // Canal G
            bool isNewG;
            vec2 offG;
            if (scanDir == 0) { isNewG = uv.x > 1.0-sG; offG = vec2(sG, 0); }
            else if (scanDir == 1) { isNewG = uv.x < sG; offG = vec2(-sG, 0); }
            else if (scanDir == 2) { isNewG = uv.y < sG; offG = vec2(0, -sG); }
            else { isNewG = uv.y > 1.0-sG; offG = vec2(0, sG); }

            if (isNewG) {
                finalCol.g = mix(IMG_NORM_PIXEL(inputImage, (scanDir<2) ? vec2(defSlit, uv.y) : vec2(uv.x, defSlit)).g, IMG_NORM_PIXEL(slitBuffer, uv).g, feedbackAmount);
            } else {
                finalCol.g = IMG_NORM_PIXEL(slitBuffer, uv + offG).g;
            }

            // Canal B
            bool isNewB;
            vec2 offB;
            if (scanDir == 0) { isNewB = uv.x > 1.0-sB; offB = vec2(sB, 0); }
            else if (scanDir == 1) { isNewB = uv.x < sB; offB = vec2(-sB, 0); }
            else if (scanDir == 2) { isNewB = uv.y < sB; offB = vec2(0, -sB); }
            else { isNewB = uv.y > 1.0-sB; offB = vec2(0, sB); }

            if (isNewB) {
                finalCol.b = mix(IMG_NORM_PIXEL(inputImage, (scanDir<2) ? vec2(defSlit, uv.y) : vec2(uv.x, defSlit)).b, IMG_NORM_PIXEL(slitBuffer, uv).b, feedbackAmount);
            } else {
                finalCol.b = IMG_NORM_PIXEL(slitBuffer, uv + offB).b;
            }

            if (abs(hueShift) > 0.01) {
                vec3 hsv = rgb2hsv(finalCol.rgb);
                hsv.x = fract(hsv.x + hueShift * 0.01);
                finalCol.rgb = hsv2rgb(hsv);
            }

            finalCol.a = 1.0;
            gl_FragColor = finalCol;
        }
    } else {
        gl_FragColor = IMG_NORM_PIXEL(slitBuffer, uv);
    }
}'''


class IsfGnSlitScan(GlslEffect):
    name = 'isf_gn_slit_scan'
    label = 'Gn Slit scan'
    description = '"Motor de Slit-Scan avanzado con 4 direcciones de escaneo, deformación por mapas (gradientes/capas externas), separación RGB temporal y feedback persistente...'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'feedback'
    scale = 0.65
    params = effect_params({
        "map_mode": {
            "type": "integer",
            "enum": [
                0,
                1,
                2,
                3
            ],
            "default": 1,
            "x-ui": {
                "label": "Map Source",
                "widget": "select",
                "labels": [
                    "Horizontal Gradient",
                    "Vertical Gradient",
                    "Circular Gradient",
                    "External Layer"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "mapMode"
            }
        },
        "map_influence": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 2.0,
            "default": 0.2,
            "x-ui": {
                "label": "Map Influence",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mapInfluence"
            }
        },
        "map_smoothing": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 3.0,
            "default": 0.5,
            "x-ui": {
                "label": "Map Curve Smoothing",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mapSmoothing"
            }
        },
        "map_blur": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.02,
            "default": 0.005,
            "x-ui": {
                "label": "Map Luma Blur",
                "step": 0.0001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mapBlur"
            }
        },
        "map_phase": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Map Phase / Offset",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mapPhase"
            }
        },
        "scan_dir": {
            "type": "integer",
            "enum": [
                0,
                1,
                2,
                3
            ],
            "default": 1,
            "x-ui": {
                "label": "Scan Direction",
                "widget": "select",
                "labels": [
                    "Moves Left",
                    "Moves Right",
                    "Moves Up",
                    "Moves Down"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "scanDir"
            }
        },
        "slit_pos": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Base Slit Position",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "slitPos"
            }
        },
        "p_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.01,
            "default": 0.002,
            "x-ui": {
                "label": "Scan Speed",
                "step": 0.0001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "speed"
            }
        },
        "rgb_split": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.02,
            "default": 0.002,
            "x-ui": {
                "label": "RGB Split (Temporal)",
                "step": 0.0001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "rgbSplit"
            }
        },
        "feedback_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.95,
            "default": 0.0,
            "x-ui": {
                "label": "Feedback (Ghosting)",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "feedbackAmount"
            }
        },
        "hue_shift": {
            "type": "number",
            "minimum": -5.0,
            "maximum": 5.0,
            "default": 0.0,
            "x-ui": {
                "label": "Feedback Hue Shift",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "hueShift"
            }
        },
        "clean_buffer": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "CLEAN BUFFER"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "cleanBuffer"
            }
        }
    })
