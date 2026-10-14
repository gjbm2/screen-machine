"""Line Move Alpha V1 (P.O.): Moving Lines

Source: https://github.com/ProjectileObjects/MiscISFShaders/blob/HEAD/Line%20Move%20Alpha%20V1%20(P.O.).fs
Author: ProjectileObjects
License: MIT
license_class: permissive
Imported verbatim from GLSL (ProjectileObjects/MiscISFShaders); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository ProjectileObjects/MiscISFShaders LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*{
    "CATEGORIES": ["Generators"],
    "CREDIT": "ProjectileObjects",
    "DESCRIPTION": "Moving Lines",
    "INPUTS": [
        {
            "NAME": "imageInput",
            "TYPE": "image"
        },
        {
            "DEFAULT": 2,
            "MAX": 100,
            "MIN": 1,
            "NAME": "lineWidth",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1,
            "MAX": 10,
            "MIN": 0.1,
            "NAME": "speed",
            "TYPE": "float"
        },
        {
            "DEFAULT": 10,
            "MAX": 50,
            "MIN": 1,
            "NAME": "numLines",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.0,
            "MAX": 6.28319,
            "MIN": 0.0,
            "NAME": "rotation",
            "TYPE": "float"
        },
        {
    "DEFAULT": 0.5,
    "MAX": 100.0,
    "MIN": 0.0,
    "NAME": "feather",
    "TYPE": "float"
}
    ],
    "ISFVSN": "2"
}
*/

void main() {
    float spacing = RENDERSIZE.x / numLines;
    float doubleLineWidth = 2.0 * lineWidth;

    vec2 centeredCoord = gl_FragCoord.xy - RENDERSIZE.xy * 0.5;

    // Rotation transformation
    float cosR = cos(rotation);
    float sinR = sin(rotation);
    vec2 rotatedCoord = vec2(centeredCoord.x * cosR - centeredCoord.y * sinR, 
                             centeredCoord.x * sinR + centeredCoord.y * cosR);

    rotatedCoord += RENDERSIZE.xy * 0.5;

    float x = rotatedCoord.x;
    float s = spacing - mod(float(FRAMEINDEX) * speed, spacing);

    float distToLine = abs(mod(x + s, spacing) - lineWidth / 2.0);

    // Apply feathering while keeping lines thick
    float featheredAlpha = clamp(1.0 - (distToLine - (lineWidth / 2.0)) / feather, 0.0, 1.0);
    
    vec2 normalizedCoord = gl_FragCoord.xy / RENDERSIZE.xy;
    vec4 imageColor = IMG_NORM_PIXEL(imageInput, normalizedCoord);

    gl_FragColor = mix(imageColor, vec4(0.0, 0.0, 0.0, 0.0), featheredAlpha);
}'''


class IsfLineMoveAlphaV1PO(GlslSource):
    name = 'isf_line_move_alpha_v1_p_o'
    label = 'Line Move Alpha V1 (P.O.)'
    description = 'Moving Lines'
    dialect = 'isf'
    GLSL = GLSL
    params = scene_params({
        "line_width": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 100.0,
            "default": 2.0,
            "x-ui": {
                "label": "Line Width",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "lineWidth"
            }
        },
        "p_speed": {
            "type": "number",
            "minimum": 0.1,
            "maximum": 10.0,
            "default": 1.0,
            "x-ui": {
                "label": "Speed",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "speed"
            }
        },
        "num_lines": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 50.0,
            "default": 10.0,
            "x-ui": {
                "label": "Num Lines",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "numLines"
            }
        },
        "rotation": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 6.28319,
            "default": 0.0,
            "x-ui": {
                "label": "Rotation",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "rotation"
            }
        },
        "feather": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
            "default": 0.5,
            "x-ui": {
                "label": "Feather",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "feather"
            }
        }
    }, render_scale=0.5)
