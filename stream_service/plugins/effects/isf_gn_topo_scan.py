"""Gn Topo Scan: "Generador de isolíneas topográficas basadas en luminancia. Incluye motor de pre-procesado Blur/Sharpen para suavizar o enfocar el mapa, animación de erosión...

Source: https://github.com/Gnomalab/VDMX-ISF-Effects-Suite/blob/HEAD/ISF/Gn%20Topo%20Scan/Gn%20Topo%20Scan.fs
Author: Gnomalab
License: MIT
license_class: permissive
Imported verbatim from GLSL (Gnomalab/VDMX-ISF-Effects-Suite); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Gnomalab/VDMX-ISF-Effects-Suite LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Geometry",
        "Stylize",
        "Gnomalab"
    ],
    "CREDIT": "Gnomalab",
    "DESCRIPTION": "\"Generador de isolíneas topográficas basadas en luminancia. Incluye motor de pre-procesado Blur/Sharpen para suavizar o enfocar el mapa, animación de erosión rítmica y modo de mezcla estilo HUD.\"",
    "INPUTS": [
        {
            "LABEL": "Source Image",
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 20,
            "LABEL": "Número de Niveles",
            "MAX": 100,
            "MIN": 2,
            "NAME": "levels",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.08,
            "LABEL": "Grosor de Línea",
            "MAX": 0.5,
            "MIN": 0.001,
            "NAME": "lineThickness",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.2,
            "LABEL": "Suavizado Bordes",
            "MAX": 1,
            "MIN": 0,
            "NAME": "softness",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.5,
            "LABEL": "Velocidad Erosión",
            "MAX": 2,
            "MIN": -2,
            "NAME": "speed",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Desplase de Altura",
            "MAX": 1,
            "MIN": 0,
            "NAME": "offset",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Definición (Blur <-> Sharpen)",
            "MAX": 1,
            "MIN": -1,
            "NAME": "preProcess",
            "TYPE": "float"
        },
        {
            "DEFAULT": [
                1,
                1,
                1,
                1
            ],
            "LABEL": "Color de Línea",
            "NAME": "lineColor",
            "TYPE": "color"
        },
        {
            "DEFAULT": 0,
            "LABEL": "Mezcla con Original (HUD)",
            "MAX": 1,
            "MIN": 0,
            "NAME": "showSource",
            "TYPE": "float"
        },
        {
            "DEFAULT": false,
            "LABEL": "Invertir Mapa",
            "NAME": "invert",
            "TYPE": "bool"
        }
    ],
    "ISFVSN": "2",
    "VSN": "1.0.0"
}
*/

// Función auxiliar para calcular luminancia
float getLuma(vec4 col) {
    return dot(col.rgb, vec3(0.299, 0.587, 0.114));
}

void main() {
    vec2 uv = isf_FragNormCoord.xy;
    vec2 texel = 1.0 / RENDERSIZE.xy;
    vec4 src = IMG_NORM_PIXEL(inputImage, uv);
    float centerLuma = getLuma(src);
    
    // --- ETAPA DE PRE-PROCESADO (BLUR / SHARPEN) ---
    float finalLuma = centerLuma;
    
    // Solo ejecutamos el bucle si el deslizador no está en el centro
    if (abs(preProcess) > 0.01) {
        // 1. Calculamos una versión desenfocada (Blur de 9 muestras)
        // El radio del blur depende de cuánto movamos el deslizador
        float radius = abs(preProcess) * 4.0; 
        vec2 offX = vec2(texel.x * radius, 0.0);
        vec2 offY = vec2(0.0, texel.y * radius);
        
        float accumLuma = 0.0;
        for(int i = -1; i <= 1; i++){
            for(int j = -1; j <= 1; j++){
                 vec2 sampleUV = clamp(uv + vec2(float(i)*offX.x, float(j)*offY.y), 0.0, 1.0);
                 float sampleLuma = getLuma(IMG_NORM_PIXEL(inputImage, sampleUV));
                 accumLuma += sampleLuma;
            }
        }
        float blurredLuma = accumLuma / 9.0;

        // 2. Aplicamos la lógica según la dirección del deslizador
        if (preProcess < 0.0) {
            // Modo BLUR: Mezclamos la luminancia original con la desenfocada
            finalLuma = mix(centerLuma, blurredLuma, abs(preProcess));
        } else {
            // Modo SHARPEN (Técnica "Unsharp Mask"):
            // Restamos el blur del original para aislar los detalles, y los sumamos de nuevo.
            float detail = centerLuma - blurredLuma;
            // Multiplicamos por 3.0 para darle agresividad al sharpen
            finalLuma = centerLuma + detail * preProcess * 3.0; 
        }
    }

    // --- ETAPA TOPOGRÁFICA ---
    float lumaMap = invert ? 1.0 - finalLuma : finalLuma;
    
    // Erosión temporal
    float height = lumaMap + (TIME * speed * 0.1) + offset;
    
    // Generación de curvas
    // Usamos fract() en lugar de sin() para líneas más técnicas y precisas
    float topo = fract(height * levels);
    
    // Definición del grosor
    float halfThick = lineThickness * 0.5;
    float s = softness * 0.1 + 0.001; // Suavizado mínimo para evitar aliasing
    
    // Creamos la línea detectando cuando el valor fract() está cerca de 0 o 1
    // Usamos dos smoothsteps para crear ambos lados de la línea
    float maskA = smoothstep(halfThick + s, halfThick, topo);
    float maskB = smoothstep(1.0 - halfThick - s, 1.0 - halfThick, topo);
    float mask = max(maskA, maskB);
    
    // --- COMPOSICIÓN FINAL ---
    vec3 finalRGB = mix(vec3(0.0), lineColor.rgb, mask);
    
    // Mezcla tipo HUD con la imagen original
    finalRGB = mix(finalRGB, src.rgb + finalRGB, showSource);
    
    gl_FragColor = vec4(finalRGB, 1.0);
}'''


class IsfGnTopoScan(GlslEffect):
    name = 'isf_gn_topo_scan'
    label = 'Gn Topo Scan'
    description = '"Generador de isolíneas topográficas basadas en luminancia. Incluye motor de pre-procesado Blur/Sharpen para suavizar o enfocar el mapa, animación de erosión...'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    params = effect_params({
        "levels": {
            "type": "number",
            "minimum": 2.0,
            "maximum": 100.0,
            "default": 20.0,
            "x-ui": {
                "label": "Número de Niveles",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "levels"
            }
        },
        "line_thickness": {
            "type": "number",
            "minimum": 0.001,
            "maximum": 0.5,
            "default": 0.08,
            "x-ui": {
                "label": "Grosor de Línea",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "lineThickness"
            }
        },
        "softness": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.2,
            "x-ui": {
                "label": "Suavizado Bordes",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "softness"
            }
        },
        "p_speed": {
            "type": "number",
            "minimum": -2.0,
            "maximum": 2.0,
            "default": 0.5,
            "x-ui": {
                "label": "Velocidad Erosión",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "speed"
            }
        },
        "offset": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Desplase de Altura",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "offset"
            }
        },
        "pre_process": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Definición (Blur <-> Sharpen)",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "preProcess"
            }
        },
        "line_color": {
            "type": "string",
            "format": "color",
            "default": "#ffffff",
            "x-ui": {
                "label": "Color de Línea",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "lineColor",
                "color": 4,
                "alpha": 1.0
            }
        },
        "show_source": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Mezcla con Original (HUD)",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "showSource"
            }
        },
        "invert": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Invertir Mapa"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "invert"
            }
        }
    })
