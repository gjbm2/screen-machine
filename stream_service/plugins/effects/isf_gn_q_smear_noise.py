"""Gn Q Smear Noise: "Smear caótico con ángulos cuantizados por brillo. Incluye ruido espacial/temporal y un motor de feedback inestable que genera texturas de glitch orgánico y...

Source: https://github.com/Gnomalab/VDMX-ISF-Effects-Suite/blob/HEAD/ISF/Gn%20Q-Smear%20Noise/Gn%20Q-Smear%20Noise.fs
Author: Gnomalab
License: MIT
license_class: permissive
Imported verbatim from GLSL (Gnomalab/VDMX-ISF-Effects-Suite); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository Gnomalab/VDMX-ISF-Effects-Suite LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "DistortionEffect",
        "Glitch"
    ],
    "CREDIT": "Gnomalab",
    "DESCRIPTION": "\"Smear caótico con ángulos cuantizados por brillo. Incluye ruido espacial/temporal y un motor de feedback inestable que genera texturas de glitch orgánico y estéticas similares al datamosh.\"",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "DEFAULT": 0.45,
            "LABEL": "Brightness Threshold",
            "MAX": 1,
            "MIN": 0,
            "NAME": "brightThreshold",
            "TYPE": "float"
        },
        {
            "DEFAULT": 18,
            "LABEL": "Smear Velocity",
            "MAX": 50,
            "MIN": -50,
            "NAME": "velocity",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.4,
            "LABEL": "Velocity Sustain",
            "MAX": 0.6,
            "MIN": 0,
            "NAME": "velocityDecay",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.97,
            "LABEL": "Colour Sustain",
            "MAX": 1,
            "MIN": 0,
            "NAME": "colourDecay",
            "TYPE": "float"
        },
        {
            "DEFAULT": 24,
            "LABEL": "Max Angular Steps",
            "MAX": 64,
            "MIN": 1,
            "NAME": "maxAngleSteps",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.6,
            "LABEL": "Temporal Noise Amount",
            "MAX": 2,
            "MIN": 0,
            "NAME": "timeNoiseAmt",
            "TYPE": "float"
        },
        {
            "DEFAULT": 1.2,
            "LABEL": "Temporal Noise Speed",
            "MAX": 5,
            "MIN": 0,
            "NAME": "timeNoiseSpeed",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.4,
            "LABEL": "Spatial Noise Amount",
            "MAX": 2,
            "MIN": 0,
            "NAME": "spatialNoiseAmt",
            "TYPE": "float"
        },
        {
            "DEFAULT": 0.15,
            "LABEL": "Feedback Instability",
            "MAX": 1,
            "MIN": 0,
            "NAME": "feedbackChaos",
            "TYPE": "float"
        }
    ],
    "ISFVSN": "2",
    "PASSES": [
        {
            "HEIGHT": "$HEIGHT",
            "PERSISTENT": true,
            "TARGET": "decayBuffer",
            "WIDTH": "$WIDTH"
        },
        {
            "PERSISTENT": true,
            "TARGET": "outputPass"
        }
    ],
    "VSN": "1.0.0"
}
*/

// ----------------------------------------------------
// Utils
// ----------------------------------------------------

float brightness(vec4 c)
{
    return (c.r + c.g + c.b) / 3.0;
}

float hash(vec2 p)
{
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

float timeNoise(float t)
{
    return fract(sin(t * 13.37) * 43758.5453);
}

// ----------------------------------------------------
// Main
// ----------------------------------------------------

void main()
{
    vec4 inputPixel = IMG_THIS_PIXEL(inputImage);
    float inBright = brightness(inputPixel);
    float decayBright = brightness(IMG_THIS_PIXEL(decayBuffer));

    // ---------------- PASS 0 : velocity memory ----------------
    if (PASSINDEX == 0)
    {
        float v = max(inBright, decayBright) * velocityDecay;
        gl_FragColor = vec4(vec3(v), 1.0);
    }

    // ---------------- PASS 1 : output ----------------
    else
    {
        if (inBright > brightThreshold)
        {
            gl_FragColor = IMG_THIS_NORM_PIXEL(inputImage);
        }
        else
        {
            // --- base chaotic angle ---
            float angle = (inBright + decayBright) * 6.28318;

            // --- temporal noise ---
            angle += timeNoise(TIME * timeNoiseSpeed) * timeNoiseAmt;

            // --- spatial noise ---
            float sNoise = hash(gl_FragCoord.xy * 0.02) * spatialNoiseAmt;
            angle += sNoise;

            // --- brightness dependent quantize ---
            float steps = mix(maxAngleSteps, 1.0, inBright);
            float stepSize = 6.28318 / max(steps, 1.0);
            angle = floor(angle / stepSize) * stepSize;

            vec2 dir = vec2(cos(angle), sin(angle));

            // --- unstable feedback offset (datamosh-ish) ---
            vec2 chaosOffset = dir * decayBright * velocity;
            chaosOffset += feedbackChaos * vec2(
                hash(vec2(TIME, gl_FragCoord.y)),
                hash(vec2(gl_FragCoord.x, TIME))
            ) * 20.0;

            vec2 loc = gl_FragCoord.xy + chaosOffset;

            vec4 smear = IMG_PIXEL(outputPass, loc);
            vec4 base  = IMG_THIS_PIXEL(outputPass);

            float mixVal = inBright * colourDecay;
            gl_FragColor = mix(smear, base, mixVal);
        }
    }
}'''


class IsfGnQSmearNoise(GlslEffect):
    name = 'isf_gn_q_smear_noise'
    label = 'Gn Q Smear Noise'
    description = '"Smear caótico con ángulos cuantizados por brillo. Incluye ruido espacial/temporal y un motor de feedback inestable que genera texturas de glitch orgánico y...'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'glitch'
    scale = 0.4
    params = effect_params({
        "bright_threshold": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.45,
            "x-ui": {
                "label": "Brightness Threshold",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "brightThreshold"
            }
        },
        "velocity": {
            "type": "number",
            "minimum": -50.0,
            "maximum": 50.0,
            "default": 18.0,
            "x-ui": {
                "label": "Smear Velocity",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "velocity"
            }
        },
        "velocity_decay": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 0.6,
            "default": 0.4,
            "x-ui": {
                "label": "Velocity Sustain",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "velocityDecay"
            }
        },
        "colour_decay": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.97,
            "x-ui": {
                "label": "Colour Sustain",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "colourDecay"
            }
        },
        "max_angle_steps": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 64.0,
            "default": 24.0,
            "x-ui": {
                "label": "Max Angular Steps",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "maxAngleSteps"
            }
        },
        "time_noise_amt": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.6,
            "x-ui": {
                "label": "Temporal Noise Amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "timeNoiseAmt"
            }
        },
        "time_noise_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 5.0,
            "default": 1.2,
            "x-ui": {
                "label": "Temporal Noise Speed",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "timeNoiseSpeed"
            }
        },
        "spatial_noise_amt": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.4,
            "x-ui": {
                "label": "Spatial Noise Amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "spatialNoiseAmt"
            }
        },
        "feedback_chaos": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.15,
            "x-ui": {
                "label": "Feedback Instability",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "feedbackChaos"
            }
        }
    })
