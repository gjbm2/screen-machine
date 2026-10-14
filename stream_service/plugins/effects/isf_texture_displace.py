"""Texture Displace: Created by ProjectileObjects. Applies analytical texture displacement using inputImage as the source.

Source: https://github.com/ProjectileObjects/MiscISFShaders/blob/HEAD/Texture_Displace.fs
Author: ProjectileObjects
License: MIT
license_class: permissive
Imported verbatim from GLSL (ProjectileObjects/MiscISFShaders); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository ProjectileObjects/MiscISFShaders LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Effect", "Displacement"
    ],
    "DESCRIPTION": "Created by ProjectileObjects. Applies analytical texture displacement using inputImage as the source.",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "NAME": "displacementStrength",
            "LABEL": "Displacement Strength",
            "TYPE": "float",
            "DEFAULT": 0.05,
            "MIN": 0.0,
            "MAX": 0.2
        },
        {
            "NAME": "frequency",
            "LABEL": "Wave Frequency",
            "TYPE": "float",
            "DEFAULT": 10.0,
            "MIN": 1.0,
            "MAX": 50.0
        },
        {
            "NAME": "timeFactor",
            "LABEL": "Time Factor",
            "TYPE": "float",
            "DEFAULT": 1.0,
            "MIN": 0.0,
            "MAX": 5.0
        }
    ],
    "ISFVSN": "2"
}*/

void main() {
    // Normalize texture coordinates
    vec2 uv = gl_FragCoord.xy / RENDERSIZE.xy;
    
    // Compute displacement analytically using sine waves
    float displacementX = sin(uv.y * frequency + TIME * timeFactor) * displacementStrength;
    float displacementY = cos(uv.x * frequency + TIME * timeFactor) * displacementStrength;
    
    // Apply displacement
    vec2 displacedUV = uv + vec2(displacementX, displacementY);
    
    // Sample the input image with displaced coordinates
    vec4 color = IMG_NORM_PIXEL(inputImage, displacedUV);
    
    // Output final color
    gl_FragColor = color;
}
'''


class IsfTextureDisplace(GlslEffect):
    name = 'isf_texture_displace'
    label = 'Texture Displace'
    description = 'Created by ProjectileObjects. Applies analytical texture displacement using inputImage as the source.'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'warp'
    params = effect_params({
        "displacement_strength": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.2,
            "default": 0.05,
            "x-ui": {
                "label": "Displacement Strength",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "displacementStrength"
            }
        },
        "frequency": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 50.0,
            "default": 10.0,
            "x-ui": {
                "label": "Wave Frequency",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "frequency"
            }
        },
        "time_factor": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 5.0,
            "default": 1.0,
            "x-ui": {
                "label": "Time Factor",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "timeFactor"
            }
        }
    })
