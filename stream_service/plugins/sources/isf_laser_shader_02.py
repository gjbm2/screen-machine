"""Laser Shader 02: Animated wave with customizable shape, colors, movement, and rotation.

Source: https://github.com/ProjectileObjects/MiscISFShaders/blob/HEAD/LaserShader_02.fs
Author: Cornelius // ProjectileObjects
License: MIT
license_class: permissive
Imported verbatim from GLSL (ProjectileObjects/MiscISFShaders); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository ProjectileObjects/MiscISFShaders LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*{
    "DESCRIPTION": "Animated wave with customizable shape, colors, movement, and rotation.",
    "CREDIT": "Cornelius // ProjectileObjects",
    "ISFVSN": "2.0",
    "CATEGORIES": [ "Generator" ],
    "INPUTS": [
        { "NAME": "amplitude", "TYPE": "float", "DEFAULT": 0.2, "MIN": 0.05, "MAX": 0.5 },
        { "NAME": "wavelength", "TYPE": "float", "DEFAULT": 4.0, "MIN": 1.0, "MAX": 20.0 },
        { "NAME": "randomSeed", "TYPE": "float", "DEFAULT": 0.0, "MIN": 0.0, "MAX": 10.0 },
        { "NAME": "position", "TYPE": "point2D", "DEFAULT": [0.0, 0.0], "MIN": [-1.0, -1.0], "MAX": [1.0, 1.0] },
        { "NAME": "rotation", "TYPE": "float", "DEFAULT": 0.0, "MIN": 0.0, "MAX": 360.0 },
        { "NAME": "moveSpeed", "TYPE": "float", "DEFAULT": 1.0, "MIN": 0.0, "MAX": 5.0 },
        { "NAME": "reverseWave", "TYPE": "bool", "DEFAULT": false },
        { "NAME": "moveAmount", "TYPE": "float", "DEFAULT": 0.05, "MIN": 0.0, "MAX": 0.3 },
        { "NAME": "rotationSpeed", "TYPE": "float", "DEFAULT": 30.0, "MIN": 0.0, "MAX": 180.0 },
        { "NAME": "reverseRotation", "TYPE": "bool", "DEFAULT": false },
        { "NAME": "color1", "TYPE": "color", "DEFAULT": [1.0, 0.0, 0.0, 1.0] },
        { "NAME": "color2", "TYPE": "color", "DEFAULT": [1.0, 0.5, 0.0, 1.0] },
        { "NAME": "color3", "TYPE": "color", "DEFAULT": [1.0, 1.0, 0.0, 1.0] },
        { "NAME": "color4", "TYPE": "color", "DEFAULT": [0.0, 1.0, 0.0, 1.0] },
        { "NAME": "color5", "TYPE": "color", "DEFAULT": [0.0, 0.0, 1.0, 1.0] },
        { "NAME": "color6", "TYPE": "color", "DEFAULT": [0.5, 0.0, 1.0, 1.0] }
    ]
}*/

float random(vec2 st) {
    return fract(sin(dot(st.xy, vec2(12.9898, 78.233))) * 43758.5453123);
}

void main() {
    vec2 uv = gl_FragCoord.xy / RENDERSIZE.xy;
    
    // Normalize position so (0,0) is center, (-1,-1) bottom-left, (1,1) top-right
    vec2 normPos = position * 0.5 + 0.5;
    
    // Adjust UV coordinates based on position
    vec2 centeredUV = (uv - normPos) * vec2(RENDERSIZE.x / RENDERSIZE.y, 1.0);

    // Define PI explicitly
    const float PI = 3.14159265359;

    // Reverse wave movement if toggle is enabled
    float directionMultiplier = reverseWave ? -1.0 : 1.0;
    float animatedX = centeredUV.x * wavelength + TIME * moveSpeed * directionMultiplier;

    // Apply randomization to the wave shape based on `randomSeed`
    float randomFactor = random(vec2(floor(animatedX * randomSeed), randomSeed)) * 0.5;
    float wave = sin(animatedX + randomFactor) * amplitude;

    // Define the thickness of the wave line
    float waveMask = smoothstep(0.01, 0.0, abs(centeredUV.y - wave));

    // Use wave position to determine color blend
    float rotatedAngle = mod(centeredUV.x + radians(rotation + TIME * rotationSpeed * (reverseRotation ? -1.0 : 1.0)), 2.0 * PI) / (2.0 * PI);
    float segment = rotatedAngle * 6.0; // Divide into 6 segments
    float index = floor(segment);
    float mixFactor = fract(segment);

    // Store colors in an array
    vec3 colors[6];
    colors[0] = color1.rgb;
    colors[1] = color2.rgb;
    colors[2] = color3.rgb;
    colors[3] = color4.rgb;
    colors[4] = color5.rgb;
    colors[5] = color6.rgb;

    // Smooth transition between colors
    vec3 finalColor = mix(colors[int(mod(index, 6.0))], colors[int(mod(index + 1.0, 6.0))], mixFactor);

    // Blend wave with a solid black background
    vec3 outputColor = mix(vec3(0.0), finalColor, waveMask);
    gl_FragColor = vec4(outputColor, 1.0);
}'''


class IsfLaserShader02(GlslSource):
    name = 'isf_laser_shader_02'
    label = 'Laser Shader 02'
    description = 'Animated wave with customizable shape, colors, movement, and rotation.'
    dialect = 'isf'
    GLSL = GLSL
    params = scene_params({
        "amplitude": {
            "type": "number",
            "minimum": 0.05,
            "maximum": 0.5,
            "default": 0.2,
            "x-ui": {
                "label": "Amplitude",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "amplitude"
            }
        },
        "wavelength": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 20.0,
            "default": 4.0,
            "x-ui": {
                "label": "Wavelength",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "wavelength"
            }
        },
        "random_seed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 0.0,
            "x-ui": {
                "label": "Random Seed",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "randomSeed"
            }
        },
        "position_x": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Position x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "position",
                "c": 0
            }
        },
        "position_y": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Position y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "position",
                "c": 1
            }
        },
        "rotation": {
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
                "u": "rotation"
            }
        },
        "move_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 5.0,
            "default": 1.0,
            "x-ui": {
                "label": "Move Speed",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "moveSpeed"
            }
        },
        "reverse_wave": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Reverse Wave"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "reverseWave"
            }
        },
        "move_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.3,
            "default": 0.05,
            "x-ui": {
                "label": "Move Amount",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "moveAmount"
            }
        },
        "rotation_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 180.0,
            "default": 30.0,
            "x-ui": {
                "label": "Rotation Speed",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "rotationSpeed"
            }
        },
        "reverse_rotation": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Reverse Rotation"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "reverseRotation"
            }
        },
        "color1": {
            "type": "string",
            "format": "color",
            "default": "#ff0000",
            "x-ui": {
                "label": "Color1",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "color1",
                "color": 4,
                "alpha": 1.0
            }
        },
        "color2": {
            "type": "string",
            "format": "color",
            "default": "#ff8000",
            "x-ui": {
                "label": "Color2",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "color2",
                "color": 4,
                "alpha": 1.0
            }
        },
        "color3": {
            "type": "string",
            "format": "color",
            "default": "#ffff00",
            "x-ui": {
                "label": "Color3",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "color3",
                "color": 4,
                "alpha": 1.0
            }
        },
        "color4": {
            "type": "string",
            "format": "color",
            "default": "#00ff00",
            "x-ui": {
                "label": "Color4",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "color4",
                "color": 4,
                "alpha": 1.0
            }
        },
        "color5": {
            "type": "string",
            "format": "color",
            "default": "#0000ff",
            "x-ui": {
                "label": "Color5",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "color5",
                "color": 4,
                "alpha": 1.0
            }
        },
        "color6": {
            "type": "string",
            "format": "color",
            "default": "#8000ff",
            "x-ui": {
                "label": "Color6",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "color6",
                "color": 4,
                "alpha": 1.0
            }
        }
    }, render_scale=0.5)
