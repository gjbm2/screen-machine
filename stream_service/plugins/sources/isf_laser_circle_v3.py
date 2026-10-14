"""Laser Circle v3: Animated circular line with customizable colors, movement, and toggleable rotation direction on a black background.

Source: https://github.com/ProjectileObjects/MiscISFShaders/blob/HEAD/LaserCircle%20v3.fs
Author: Cornelius // ProjectileObjects
License: MIT
license_class: permissive
Imported verbatim from GLSL (ProjectileObjects/MiscISFShaders); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository ProjectileObjects/MiscISFShaders LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*{
    "DESCRIPTION": "Animated circular line with customizable colors, movement, and toggleable rotation direction on a black background.",
    "CREDIT": "Cornelius // ProjectileObjects",
    "ISFVSN": "2.0",
    "CATEGORIES": [ "Generator" ],
    "INPUTS": [
        { "NAME": "circleSize", "TYPE": "float", "DEFAULT": 0.3, "MIN": 0.05, "MAX": 0.9 },
        { "NAME": "thickness", "TYPE": "float", "DEFAULT": 0.02, "MIN": 0.001, "MAX": 0.1 },
        { "NAME": "position", "TYPE": "point2D", "DEFAULT": [0.0, 0.0], "MIN": [-1.0, -1.0], "MAX": [1.0, 1.0] },
        { "NAME": "rotation", "TYPE": "float", "DEFAULT": 0.0, "MIN": 0.0, "MAX": 360.0 },
        { "NAME": "moveSpeed", "TYPE": "float", "DEFAULT": 1.0, "MIN": 0.0, "MAX": 5.0 },
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

void main() {
    vec2 uv = gl_FragCoord.xy / RENDERSIZE.xy;
    
    // Normalize position so (0,0) is center, (-1,-1) bottom-left, (1,1) top-right
    vec2 normPos = position * 0.5 + 0.5;
    
    // Animate movement using sine wave
    float animatedSize = circleSize + moveAmount * sin(TIME * moveSpeed);

    // Adjust UV coordinates based on position, maintain aspect ratio
    vec2 aspect = vec2(RENDERSIZE.x / RENDERSIZE.y, 1.0);
    vec2 centeredUV = (uv - normPos) * aspect;

    // Compute radius and angle in polar coordinates
    float radius = length(centeredUV);
    float angle = atan(centeredUV.y, centeredUV.x);

    // Define PI explicitly
    const float PI = 3.14159265359;

    // Determine rotation direction based on toggle button
    float directionMultiplier = reverseRotation ? -1.0 : 1.0;
    float animatedRotation = radians(rotation + TIME * rotationSpeed * directionMultiplier);
    
    float rotatedAngle = mod(angle + animatedRotation, 2.0 * PI) / (2.0 * PI);

    // Define outer and inner radius for the ring
    float outer = animatedSize;
    float inner = outer - thickness;

    // Create a smooth circular band with anti-aliasing
    float ringMask = smoothstep(inner, inner + 0.002, radius) - smoothstep(outer, outer + 0.002, radius);

    // Store color inputs into an array
    vec3 colors[6];
    colors[0] = color1.rgb;
    colors[1] = color2.rgb;
    colors[2] = color3.rgb;
    colors[3] = color4.rgb;
    colors[4] = color5.rgb;
    colors[5] = color6.rgb;

    // Use rotated angle to smoothly blend between 6 colors
    float segment = rotatedAngle * 6.0;  // Divide circle into 6 segments
    float index = floor(segment);  // Get segment index as float
    float mixFactor = fract(segment); // Smooth interpolation factor

    // Ensure index wraps correctly using mod with floating point
    vec3 finalColor = mix(colors[int(mod(index, 6.0))], colors[int(mod(index + 1.0, 6.0))], mixFactor);

    // Blend final color with a solid black background (no transparency)
    vec3 outputColor = mix(vec3(0.0), finalColor, ringMask);
    gl_FragColor = vec4(outputColor, 1.0);
}'''


class IsfLaserCircleV3(GlslSource):
    name = 'isf_laser_circle_v3'
    label = 'Laser Circle v3'
    description = 'Animated circular line with customizable colors, movement, and toggleable rotation direction on a black background.'
    dialect = 'isf'
    GLSL = GLSL
    params = scene_params({
        "circle_size": {
            "type": "number",
            "minimum": 0.05,
            "maximum": 0.9,
            "default": 0.3,
            "x-ui": {
                "label": "Circle Size",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "circleSize"
            }
        },
        "thickness": {
            "type": "number",
            "minimum": 0.001,
            "maximum": 0.1,
            "default": 0.02,
            "x-ui": {
                "label": "Thickness",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "thickness"
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
