"""Grid Move: Labyrinth effect adapted from MadMapper Laser Material

Source: https://github.com/ProjectileObjects/MiscISFShaders/blob/HEAD/GridMove.fs
Author: Converted by Cornelius // ProjectileObjects
License: MIT
license_class: permissive
Imported verbatim from GLSL (ProjectileObjects/MiscISFShaders); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository ProjectileObjects/MiscISFShaders LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*{
    "CREDIT": "Converted by Cornelius // ProjectileObjects",
    "DESCRIPTION": "Labyrinth effect adapted from MadMapper Laser Material",
    "TAGS": "graphic, laser, procedural",
    "VSN": "1.3",
    "INPUTS": [
        { "NAME": "spectrum", "TYPE": "audioFFT", "SIZE": 3 },
        { "LABEL": "Scale", "NAME": "mat_scale", "TYPE": "float", "MIN": 0.15, "MAX": 5.0, "DEFAULT": 1.5 },
        { "LABEL": "Offset", "NAME": "mat_offset", "TYPE": "point2D", "MAX": [1.0, 1.0], "MIN": [-1.0, -1.0], "DEFAULT": [0.0, 0.0] },
        { "LABEL": "Rotation", "NAME": "mat_rotation", "TYPE": "float", "MIN": 0.0, "MAX": 360.0, "DEFAULT": 0.0 },
        { "LABEL": "Auto Move", "NAME": "mat_auto", "TYPE": "bool", "DEFAULT": true },
        { "LABEL": "Auto Move Speed", "NAME": "mat_mspeed", "TYPE": "float", "MIN": 0.0, "MAX": 2.0, "DEFAULT": 0.4 },
        { "LABEL": "Auto Move Amplitude", "NAME": "mat_amplitude", "TYPE": "float", "MIN": 0.0, "MAX": 2.0, "DEFAULT": 1.0 },
        { "LABEL": "Audio Reactivity", "NAME": "mat_audio_level", "TYPE": "float", "MIN": 0.0, "MAX": 2.0, "DEFAULT": 0.0 },
        { "LABEL": "Line Thickness", "NAME": "mat_line_thickness", "TYPE": "float", "MIN": 0.005, "MAX": 0.05, "DEFAULT": 0.015 },
        { "LABEL": "Pattern Complexity", "NAME": "mat_pattern_complexity", "TYPE": "float", "MIN": 1.0, "MAX": 10.0, "DEFAULT": 5.0 },
        { "LABEL": "Main Color", "NAME": "mat_mainColor", "TYPE": "color", "DEFAULT": [1.0, 1.0, 1.0, 1.0] },
        { "LABEL": "Background Color", "NAME": "mat_bgColor", "TYPE": "color", "DEFAULT": [0.0, 0.0, 0.0, 1.0] },
        { "LABEL": "Strobe Active", "NAME": "mat_strobeActivated", "TYPE": "bool", "DEFAULT": false },
        { "LABEL": "Strobe Speed", "NAME": "mat_speedStrobe", "TYPE": "float", "MIN": 0.0, "MAX": 1.0, "DEFAULT": 0.6 },
        { "LABEL": "Strobe Duration", "NAME": "mat_strobeDuration", "TYPE": "float", "MIN": 0.0, "MAX": 1.0, "DEFAULT": 0.25 }
    ]
}*/

mat2 rot(float a) {
    float c = cos(a);
    float s = sin(a);
    return mat2(c, s, -s, c);
}

float labyrinthPattern(vec2 uv, float complexity) {
    uv = abs(fract(uv * complexity) - 0.5);
    return smoothstep(0.1, 0.02, min(uv.x, uv.y));
}

void main() {
    vec2 uv = gl_FragCoord.xy / RENDERSIZE.xy;
    uv = uv * 2.0 - 1.0;
    uv.x *= RENDERSIZE.x / RENDERSIZE.y;
    
    float alteredTime = TIME * mat_mspeed;
    if (mat_auto) {
        uv += vec2(sin(alteredTime) * mat_amplitude, cos(alteredTime) * mat_amplitude);
    }
    
    uv *= rot(radians(mat_rotation));
    uv /= mat_scale * 0.5;
    uv += mat_offset;
    
    float pattern = labyrinthPattern(uv, mat_pattern_complexity);
    vec3 color = mix(mat_bgColor.rgb, mat_mainColor.rgb, pattern);
    
    if (mat_strobeActivated) {
        float strobe = fract(TIME * mat_speedStrobe) < mat_strobeDuration ? 1.0 : 0.0;
        color *= strobe;
    }
    
    gl_FragColor = vec4(color, mat_mainColor.a);
}
'''


class IsfGridMove(GlslSource):
    name = 'isf_grid_move'
    label = 'Grid Move'
    description = 'Labyrinth effect adapted from MadMapper Laser Material'
    dialect = 'isf'
    GLSL = GLSL
    params = scene_params({
        "mat_scale": {
            "type": "number",
            "minimum": 0.15,
            "maximum": 5.0,
            "default": 1.5,
            "x-ui": {
                "label": "Scale",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mat_scale"
            }
        },
        "mat_offset_x": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Offset x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mat_offset",
                "c": 0
            }
        },
        "mat_offset_y": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Offset y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mat_offset",
                "c": 1
            }
        },
        "mat_rotation": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 360.0,
            "default": 0.0,
            "x-ui": {
                "label": "Rotation",
                "step": 1.0,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mat_rotation"
            }
        },
        "mat_auto": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Auto Move"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mat_auto"
            }
        },
        "mat_mspeed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.4,
            "x-ui": {
                "label": "Auto Move Speed",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mat_mspeed"
            }
        },
        "mat_amplitude": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 1.0,
            "x-ui": {
                "label": "Auto Move Amplitude",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mat_amplitude"
            }
        },
        "mat_audio_level": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.0,
            "x-ui": {
                "label": "Audio Reactivity",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mat_audio_level"
            }
        },
        "mat_line_thickness": {
            "type": "number",
            "minimum": 0.005,
            "maximum": 0.05,
            "default": 0.015,
            "x-ui": {
                "label": "Line Thickness",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mat_line_thickness"
            }
        },
        "mat_pattern_complexity": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 10.0,
            "default": 5.0,
            "x-ui": {
                "label": "Pattern Complexity",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mat_pattern_complexity"
            }
        },
        "mat_main_color": {
            "type": "string",
            "format": "color",
            "default": "#ffffff",
            "x-ui": {
                "label": "Main Color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mat_mainColor",
                "color": 4,
                "alpha": 1.0
            }
        },
        "mat_bg_color": {
            "type": "string",
            "format": "color",
            "default": "#000000",
            "x-ui": {
                "label": "Background Color",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mat_bgColor",
                "color": 4,
                "alpha": 1.0
            }
        },
        "mat_strobe_activated": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Strobe Active"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mat_strobeActivated"
            }
        },
        "mat_speed_strobe": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.6,
            "x-ui": {
                "label": "Strobe Speed",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mat_speedStrobe"
            }
        },
        "mat_strobe_duration": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.25,
            "x-ui": {
                "label": "Strobe Duration",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mat_strobeDuration"
            }
        }
    }, render_scale=0.5)
