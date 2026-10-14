"""Analog Glitch 9: Created by ProjectileObjects. Analog glitch with scanline warp, vertical roll, directional skew, and sync distortion.

Source: https://github.com/ProjectileObjects/MiscISFShaders/blob/HEAD/Analog%20Glitch%209.fs
Author: ProjectileObjects
License: MIT
license_class: permissive
Imported verbatim from GLSL (ProjectileObjects/MiscISFShaders); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository ProjectileObjects/MiscISFShaders LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
  "CATEGORIES": ["Stylize", "Glitch", "Analog"],
  "DESCRIPTION": "Created by ProjectileObjects. Analog glitch with scanline warp, vertical roll, directional skew, and sync distortion.",
  "ISFVSN": "2",
  "INPUTS": [
    { "NAME": "inputImage", "TYPE": "image" },
    {
      "NAME": "timeSpeed", "TYPE": "float", "DEFAULT": 1.0,
      "MIN": 0.0, "MAX": 5.0, "LABEL": "Time Speed"
    },
    {
      "NAME": "glitchIntensity", "TYPE": "float", "DEFAULT": 0.3,
      "MIN": 0.0, "MAX": 1.0, "LABEL": "Glitch Intensity"
    },
    {
      "NAME": "colorSeparation", "TYPE": "float", "DEFAULT": 0.02,
      "MIN": 0.0, "MAX": 0.1, "LABEL": "Color Separation"
    },
    {
      "NAME": "verticalRollSpeed", "TYPE": "float", "DEFAULT": 0.2,
      "MIN": 0.0, "MAX": 2.0, "LABEL": "Vertical Roll Speed"
    },
    {
      "NAME": "interlaced", "TYPE": "bool", "DEFAULT": true,
      "LABEL": "Interlaced Effect"
    },
    {
      "NAME": "interlaceStrength", "TYPE": "float", "DEFAULT": 0.3,
      "MIN": 0.0, "MAX": 1.0, "LABEL": "Interlace Strength"
    },
    {
      "NAME": "interlaceWarpAmount", "TYPE": "float", "DEFAULT": 0.05,
      "MIN": 0.0, "MAX": 0.3, "LABEL": "Interlace Warp Amount"
    },
    {
      "NAME": "warpCenter", "TYPE": "float", "DEFAULT": 0.5,
      "MIN": 0.0, "MAX": 1.0, "LABEL": "Warp Vertical Center"
    },
    {
      "NAME": "warpRange", "TYPE": "float", "DEFAULT": 0.3,
      "MIN": 0.0, "MAX": 1.0, "LABEL": "Warp Vertical Range"
    },
    {
      "NAME": "randomizeWarpPosition", "TYPE": "bool",
      "DEFAULT": false, "LABEL": "Auto-Shift Warp Center"
    },
    {
      "NAME": "warpSeedSpeed", "TYPE": "float", "DEFAULT": 0.5,
      "MIN": 0.0, "MAX": 5.0, "LABEL": "Warp Drift Speed"
    },
    {
      "NAME": "interlaceSkewAmount", "TYPE": "float", "DEFAULT": 0.02,
      "MIN": 0.0, "MAX": 0.2, "LABEL": "Skew Strength"
    },
    {
      "NAME": "skewBias", "TYPE": "float", "DEFAULT": 0.0,
      "MIN": -1.0, "MAX": 1.0, "LABEL": "Skew Direction Bias"
    }
  ]
}*/

float hash(float x) {
    return fract(sin(x * 17.372) * 43758.5453123);
}

void main() {
    vec2 uv = isf_FragNormCoord;
    float time = TIME * timeSpeed;

    // Horizontal glitch tear
    float randLine = hash(uv.y + time * 2.0);
    float hOffset = glitchIntensity * 0.05 * randLine;

    // Interlace line toggle
    float interlaceLine = interlaced ? step(0.5, fract(uv.y * RENDERSIZE.y * 0.5)) : 1.0;
    float interlaceEffect = mix(1.0, interlaceLine, interlaceStrength);

    // Animate warp center if enabled
    float animatedWarpCenter = warpCenter;
    if (randomizeWarpPosition) {
        float noise = sin(time * warpSeedSpeed) * 0.5 + 0.5;
        animatedWarpCenter = mix(0.1, 0.9, noise);
    }

    float distFromCenter = abs(uv.y - animatedWarpCenter);
    float warpMask = smoothstep(warpRange, 0.0, distFromCenter);

    // Horizontal scanline ripple warp
    float warp = sin(uv.y * RENDERSIZE.y * 5.0 + time * 15.0) * interlaceWarpAmount * warpMask;
    float warpedX = uv.x + warp * interlaceEffect;

    // Skew with directional bias
    float lineID = floor(uv.y * RENDERSIZE.y);
    float baseJitter = hash(lineID + time * 10.0);
    float skewRandom = (baseJitter - 0.5) * 2.0;
    float skew = (skewRandom + skewBias) * 0.5;

    // Apply roll and skew
    float rolledY = uv.y + time * verticalRollSpeed;
    float skewedY = rolledY + skew * interlaceSkewAmount * warpMask;

    vec2 warpedUV = vec2(warpedX + hOffset, mod(skewedY, 1.0));

    // RGB chromatic offset
    vec2 redOffset = vec2(colorSeparation, 0.0);
    vec2 greenOffset = vec2(0.0);
    vec2 blueOffset = vec2(-colorSeparation, 0.0);

    vec4 red   = texture2D(inputImage, warpedUV + redOffset);
    vec4 green = texture2D(inputImage, warpedUV + greenOffset);
    vec4 blue  = texture2D(inputImage, warpedUV + blueOffset);

    vec4 color = vec4(red.r, green.g, blue.b, 1.0);

    // Scanline flicker
    float scanline = 0.9 + 0.1 * sin(uv.y * RENDERSIZE.y * 2.0 + time * 20.0);
    color.rgb *= scanline * interlaceEffect;

    gl_FragColor = color;
}'''


class IsfAnalogGlitch9(GlslEffect):
    name = 'isf_analog_glitch_9'
    label = 'Analog Glitch 9'
    description = 'Created by ProjectileObjects. Analog glitch with scanline warp, vertical roll, directional skew, and sync distortion.'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.85
    params = effect_params({
        "time_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 5.0,
            "default": 1.0,
            "x-ui": {
                "label": "Time Speed",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "timeSpeed"
            }
        },
        "glitch_intensity": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.3,
            "x-ui": {
                "label": "Glitch Intensity",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "glitchIntensity"
            }
        },
        "color_separation": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.1,
            "default": 0.02,
            "x-ui": {
                "label": "Color Separation",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "colorSeparation"
            }
        },
        "vertical_roll_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.2,
            "x-ui": {
                "label": "Vertical Roll Speed",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "verticalRollSpeed"
            }
        },
        "interlaced": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Interlaced Effect"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "interlaced"
            }
        },
        "interlace_strength": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.3,
            "x-ui": {
                "label": "Interlace Strength",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "interlaceStrength"
            }
        },
        "interlace_warp_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.3,
            "default": 0.05,
            "x-ui": {
                "label": "Interlace Warp Amount",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "interlaceWarpAmount"
            }
        },
        "warp_center": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Warp Vertical Center",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "warpCenter"
            }
        },
        "warp_range": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.3,
            "x-ui": {
                "label": "Warp Vertical Range",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "warpRange"
            }
        },
        "randomize_warp_position": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Auto-Shift Warp Center"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "randomizeWarpPosition"
            }
        },
        "warp_seed_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 5.0,
            "default": 0.5,
            "x-ui": {
                "label": "Warp Drift Speed",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "warpSeedSpeed"
            }
        },
        "interlace_skew_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.2,
            "default": 0.02,
            "x-ui": {
                "label": "Skew Strength",
                "step": 0.001,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "interlaceSkewAmount"
            }
        },
        "skew_bias": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Skew Direction Bias",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "skewBias"
            }
        }
    })
