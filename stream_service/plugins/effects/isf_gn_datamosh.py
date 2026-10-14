"""Gn Datamosh: fake Datamosh

Source: https://github.com/Gnomalab/VDMX-ISF-Effects-Suite/blob/HEAD/ISF/Gn%20Datamosh/Gn%20Datamosh.fs
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
        "Glitch",
        "Compression"
    ],
    "CREDIT": "Gnomalab",
    "DESCRIPTION": "fake Datamosh ",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0.7,
            "LABEL": "Fuerza Derretido",
            "MAX": 1,
            "MIN": 0,
            "NAME": "mosh_strength",
            "TYPE": "float"
        },
        {
            "DEFAULT": 16,
            "LABEL": "Tamaño Macrobloque",
            "MAX": 64,
            "MIN": 2,
            "NAME": "block_size",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.2,
            "LABEL": "Sensibilidad Mov.",
            "MAX": 1,
            "MIN": 0,
            "NAME": "motion_threshold",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Solo en Bordes",
            "MAX": 1,
            "MIN": 0,
            "NAME": "edge_boost",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Luma Min",
            "MAX": 1,
            "MIN": 0,
            "NAME": "luma_key_min",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1,
            "LABEL": "Luma Max",
            "MAX": 1,
            "MIN": 0,
            "NAME": "luma_key_max",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.98,
            "LABEL": "Persistencia Escena",
            "MAX": 1,
            "MIN": 0,
            "NAME": "persistence",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.5,
            "LABEL": "Limpieza Fondo",
            "MAX": 1,
            "MIN": 0,
            "NAME": "background_clean",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Fuerza Gravedad",
            "MAX": 0.2,
            "MIN": 0,
            "NAME": "gravity_force",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.75,
            "LABEL": "Direccion Gravedad",
            "MAX": 1,
            "MIN": 0,
            "NAME": "gravity_dir",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.3,
            "LABEL": "Ensuciar Color",
            "MAX": 1,
            "MIN": 0,
            "NAME": "color_corruption",
            "TYPE": "float"
        },
        {
            "DEFAULT": false,
            "LABEL": "Limpiar I-Frame",
            "NAME": "reset",
            "TYPE": "bool"
        },
        {
            "DEFAULT": 0.15,
            "LABEL": "Velocidad Recuperacion",
            "MAX": 1,
            "MIN": 0,
            "NAME": "sync_speed",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2",
    "PASSES": [
        {
            "PERSISTENT": true,
            "TARGET": "lastFrame"
        }
    ],
    "VSN": "1.0.0"
}
*/

vec3 rgb2yuv(vec3 rgb) {
    float y = dot(rgb, vec3(0.299, 0.587, 0.114));
    float u = dot(rgb, vec3(-0.147, -0.289, 0.436));
    float v = dot(rgb, vec3(0.615, -0.515, -0.100));
    return vec3(y, u, v);
}

vec3 yuv2rgb(vec3 yuv) {
    float r = yuv.x + 1.140 * yuv.z;
    float g = yuv.x - 0.395 * yuv.y - 0.581 * yuv.z;
    float b = yuv.x + 2.032 * yuv.y;
    return vec3(r, g, b);
}

void main() {
    vec2 uv = isf_FragNormCoord;
    float b = max(block_size, 1.0);
    vec2 bUV = floor(uv * RENDERSIZE / b) * b / RENDERSIZE;
    vec2 off = vec2(b) / RENDERSIZE;
    
    vec4 current = IMG_NORM_PIXEL(inputImage, uv);
    vec4 previous = IMG_NORM_PIXEL(lastFrame, uv);
    
    // Estimación de movimiento y bordes
    float lC = dot(IMG_NORM_PIXEL(inputImage, bUV).rgb, vec3(0.3, 0.59, 0.11));
    float lR = dot(IMG_NORM_PIXEL(inputImage, bUV + vec2(off.x, 0.0)).rgb, vec3(0.3, 0.59, 0.11));
    float lU = dot(IMG_NORM_PIXEL(inputImage, bUV + vec2(0.0, off.y)).rgb, vec3(0.3, 0.59, 0.11));
    float edge = abs(lC - lR) + abs(lC - lU);
    
    // Luma Keyer
    float pixelLuma = dot(current.rgb, vec3(0.299, 0.587, 0.114));
    bool inLumaRange = (pixelLuma >= luma_key_min && pixelLuma <= luma_key_max);
    
    // Gravedad
    vec2 motion = vec2(lC - lR, lC - lU);
    float angle = gravity_dir * 6.28318;
    motion += vec2(cos(angle), sin(angle)) * gravity_force;

    // Activación inteligente
    float motion_mag = length(motion);
    float edge_factor = mix(1.0, edge * 10.0, edge_boost);
    bool shouldMosh = (motion_mag * edge_factor > motion_threshold) && inLumaRange;

    float activePersistence = reset ? mix(persistence, 0.0, sync_speed) : persistence;
    vec4 finalCol;
    
    if (shouldMosh) {
        vec2 moshUV = uv + (motion * mosh_strength * 0.25);
        
        // Muestreo YUV Real
        vec4 sampleLuma = IMG_NORM_PIXEL(lastFrame, moshUV);
        float bChroma = b * 2.0; // Chroma subsampling factor
        vec2 chromaUV = floor(moshUV * RENDERSIZE / bChroma) * bChroma / RENDERSIZE;
        vec4 sampleChroma = IMG_NORM_PIXEL(lastFrame, chromaUV);
        
        vec3 compositeRGB = yuv2rgb(vec3(rgb2yuv(sampleLuma.rgb).x, rgb2yuv(sampleChroma.rgb).y, rgb2yuv(sampleChroma.rgb).z));

        // Corrupción de color suave (anti-quemado)
        if (color_corruption > 0.0) {
            float lVal = dot(compositeRGB, vec3(0.3, 0.59, 0.11));
            vec3 sat = mix(vec3(lVal), compositeRGB, 1.0 + color_corruption * 0.5);
            float levels = mix(256.0, 16.0, color_corruption * color_corruption);
            compositeRGB = mix(compositeRGB, floor(sat * levels) / levels, color_corruption);
        }
        
        finalCol.rgb = mix(current.rgb, clamp(compositeRGB, 0.0, 1.0), activePersistence);
    } else {
        // Anti-Smear Logic
        float bg_persistence = mix(activePersistence, 0.0, background_clean);
        finalCol = mix(current, previous, bg_persistence);
    }

    if (reset) finalCol = mix(finalCol, current, sync_speed);

    gl_FragColor = vec4(finalCol.rgb, 1.0);
}'''


class IsfGnDatamosh(GlslEffect):
    name = 'isf_gn_datamosh'
    label = 'Gn Datamosh'
    description = 'fake Datamosh'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.4
    params = effect_params({
        "mosh_strength": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.7,
            "x-ui": {
                "label": "Fuerza Derretido",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mosh_strength"
            }
        },
        "block_size": {
            "type": "number",
            "minimum": 2.0,
            "maximum": 64.0,
            "default": 16.0,
            "x-ui": {
                "label": "Tamaño Macrobloque",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "block_size"
            }
        },
        "motion_threshold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.2,
            "x-ui": {
                "label": "Sensibilidad Mov.",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "motion_threshold"
            }
        },
        "edge_boost": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Solo en Bordes",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "edge_boost"
            }
        },
        "luma_key_min": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Luma Min",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "luma_key_min"
            }
        },
        "luma_key_max": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 1.0,
            "x-ui": {
                "label": "Luma Max",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "luma_key_max"
            }
        },
        "persistence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.98,
            "x-ui": {
                "label": "Persistencia Escena",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "persistence"
            }
        },
        "background_clean": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Limpieza Fondo",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "background_clean"
            }
        },
        "gravity_force": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.2,
            "default": 0.0,
            "x-ui": {
                "label": "Fuerza Gravedad",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "gravity_force"
            }
        },
        "gravity_dir": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.75,
            "x-ui": {
                "label": "Direccion Gravedad",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "gravity_dir"
            }
        },
        "color_corruption": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.3,
            "x-ui": {
                "label": "Ensuciar Color",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "color_corruption"
            }
        },
        "reset": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Limpiar I-Frame"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "reset"
            }
        },
        "sync_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.15,
            "x-ui": {
                "label": "Velocidad Recuperacion",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "sync_speed"
            }
        }
    })
