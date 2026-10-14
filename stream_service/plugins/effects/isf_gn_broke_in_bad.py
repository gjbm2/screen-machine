"""Gn Broke in Bad: Luminance Displacement - Fine Grain Version

Source: https://github.com/Gnomalab/VDMX-ISF-Effects-Suite/blob/HEAD/ISF/Gn%20Broke%20in%20Bad/Gn%20Broke%20in%20Bad.fs
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
        "Distortion"
    ],
    "CREDIT": "Gnomalab",
    "DESCRIPTION": "Luminance Displacement - Fine Grain Version",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0.5,
            "LABEL": "Luma Threshold",
            "MAX": 1,
            "MIN": 0,
            "NAME": "threshold",
            "TYPE": "float"
        },
        {
            "DEFAULT": false,
            "LABEL": "Invert Threshold",
            "NAME": "invertLuma",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 0.05,
            "LABEL": "Distortion H",
            "MAX": 0.3,
            "MIN": 0,
            "NAME": "displacement",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Blend Mode",
            "LABELS": [
                "Replace",
                "Multiply"
            ],
            "NAME": "blendMode",
            "TYPE": "long",
            "VALUES": [
                0,
                1
            ]
        },
        {
            "DEFAULT": 0,
            "LABEL": "Pattern Style",
            "LABELS": [
                "Fine Grain",
                "Ultra Thin V",
                "Ultra Thin H"
            ],
            "NAME": "patternStyle",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2
            ]
        },
        {
            "DEFAULT": 100,
            "LABEL": "Density / Size",
            "MAX": 200,
            "MIN": 10,
            "NAME": "patternDensity",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2",
    "VSN": "1.0.0"
}
*/

float rand(vec2 co){
    return fract(sin(dot(co.xy ,vec2(12.9898,78.233))) * 43758.5453);
}

void main() {
    // 1. Coordenadas y Color Base
    vec2 uv = isf_FragNormCoord;
    vec4 src = IMG_NORM_PIXEL(inputImage, uv);
    
    // 2. Luminancia y Máscara
    float luma = dot(src.rgb, vec3(0.299, 0.587, 0.114));
    float mask = invertLuma ? step(luma, threshold) : step(threshold, luma);
    
    if (mask > 0.5) {
        // 3. Desplazamiento Fino por Filas
        // Usamos una frecuencia alta para que las filas desplazadas sean delgadas
        float row = floor(uv.y * patternDensity * 1.5);
        float noiseVal = rand(vec2(row, TIME));
        float shift = (noiseVal - 0.5) * displacement;
        
        vec2 distortedUV = fract(uv + vec2(shift, 0.0));
        vec4 distortedImg = IMG_NORM_PIXEL(inputImage, distortedUV);
        
        // 4. Patrones Ultra Finos
        float pat = 0.0;
        if (patternStyle == 0) {
            // Grano de pixel puro
            pat = rand(gl_FragCoord.xy + TIME);
        } 
        else if (patternStyle == 1) {
            // Líneas Verticales: step muy alto (0.95) para que sean hilos
            pat = step(0.95, fract(uv.x * patternDensity));
        } 
        else if (patternStyle == 2) {
            // Líneas Horizontales
            pat = step(0.95, fract(uv.y * patternDensity));
        }
        
        // 5. Mezcla Final
        if (blendMode == 0) {
            // Replace (Mezcla suave con blanco)
            gl_FragColor = mix(distortedImg, vec4(vec3(pat), 1.0), 0.3);
        } else {
            // Multiply (La imagen solo se ve donde hay patrón o se oscurece)
            gl_FragColor = distortedImg * (1.0 - (pat * 0.7));
        }
    } 
    else {
        // Fuera de la zona de luma: Imagen limpia
        gl_FragColor = src;
    }
}'''


class IsfGnBrokeInBad(GlslEffect):
    name = 'isf_gn_broke_in_bad'
    label = 'Gn Broke in Bad'
    description = 'Luminance Displacement - Fine Grain Version'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    params = effect_params({
        "threshold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Luma Threshold",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "threshold"
            }
        },
        "invert_luma": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Invert Threshold"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "invertLuma"
            }
        },
        "displacement": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.3,
            "default": 0.05,
            "x-ui": {
                "label": "Distortion H",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "displacement"
            }
        },
        "blend_mode": {
            "type": "integer",
            "enum": [
                0,
                1
            ],
            "default": 0,
            "x-ui": {
                "label": "Blend Mode",
                "widget": "select",
                "labels": [
                    "Replace",
                    "Multiply"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "blendMode"
            }
        },
        "pattern_style": {
            "type": "integer",
            "enum": [
                0,
                1,
                2
            ],
            "default": 0,
            "x-ui": {
                "label": "Pattern Style",
                "widget": "select",
                "labels": [
                    "Fine Grain",
                    "Ultra Thin V",
                    "Ultra Thin H"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "patternStyle"
            }
        },
        "pattern_density": {
            "type": "number",
            "minimum": 10.0,
            "maximum": 200.0,
            "default": 100.0,
            "x-ui": {
                "label": "Density / Size",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "patternDensity"
            }
        }
    })
