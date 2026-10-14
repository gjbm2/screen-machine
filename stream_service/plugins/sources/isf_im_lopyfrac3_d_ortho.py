"""IM LOPYFrac3 D Ortho: Complex pulsating fractal with raymarching and smoothed speed/rotation transitions. Camera is now controllable.

Source: https://github.com/bareimage/ISF/blob/HEAD/Release.4/IM-LOPYFrac3D-Ortho.fs
Author: @YoheiNishitsuji from twigl.app shader, ISF 2.0 by dot2dot
License: MIT
license_class: permissive
Imported verbatim from GLSL (bareimage/ISF); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*{
    "DESCRIPTION": "Complex pulsating fractal with raymarching and smoothed speed/rotation transitions. Camera is now controllable.",
    "CREDIT": "@YoheiNishitsuji from twigl.app shader, ISF 2.0 by dot2dot",
    "ISFVSN": "2.0",
    "CATEGORIES": [
        "Generator",
        "Fractal"
    ],
    "INPUTS": [
        {
            "NAME": "speed",
            "TYPE": "float",
            "DEFAULT": 0.5,
            "MIN": 0.0,
            "MAX": 3.0,
            "LABEL": "Pulsate Speed"
        },
        {
            "NAME": "rotationSpeedX",
            "TYPE": "float",
            "DEFAULT": 0.1,
            "MIN": -2.0,
            "MAX": 2.0,
            "LABEL": "Rotation Speed X"
        },
        {
            "NAME": "rotationSpeedY",
            "TYPE": "float",
            "DEFAULT": 0.1,
            "MIN": -2.0,
            "MAX": 2.0,
            "LABEL": "Rotation Speed Y"
        },
        {
            "NAME": "rotationSpeedZ",
            "TYPE": "float",
            "DEFAULT": 0.1,
            "MIN": -2.0,
            "MAX": 2.0,
            "LABEL": "Rotation Speed Z"
        },
        {
            "NAME": "cameraX",
            "TYPE": "float",
            "DEFAULT": 0.0,
            "MIN": -5.0,
            "MAX": 5.0,
            "LABEL": "Camera X"
        },
        {
            "NAME": "cameraY",
            "TYPE": "float",
            "DEFAULT": 0.0,
            "MIN": -5.0,
            "MAX": 5.0,
            "LABEL": "Camera Y"
        },
        {
            "NAME": "cameraZ",
            "TYPE": "float",
            "DEFAULT": 0.0,
            "MIN": -5.0,
            "MAX": 5.0,
            "LABEL": "Camera Z"
        },
        {
            "NAME": "scale",
            "TYPE": "float",
            "DEFAULT": 5.0,
            "MIN": 1.0,
            "MAX": 10.0
        },
        {
            "NAME": "brightness",
            "TYPE": "float",
            "DEFAULT": 1.0,
            "MIN": 0.1,
            "MAX": 3.0
        },
        {
            "NAME": "hue_shift",
            "TYPE": "float",
            "DEFAULT": 0.0,
            "MIN": 0.0,
            "MAX": 1.0
        },
        {
            "NAME": "fractalIterations",
            "TYPE": "long",
            "DEFAULT": 25,
            "MIN": 5,
            "MAX": 50,
            "LABEL": "Fractal Detail"
        },
        {
            "NAME": "raymarchSteps",
            "TYPE": "long",
            "DEFAULT": 30,
            "MIN": 10,
            "MAX": 80,
            "LABEL": "Render Depth"
        },
        {
            "NAME": "transitionSpeed",
            "TYPE": "float",
            "DEFAULT": 2.0,
            "MIN": 0.1,
            "MAX": 10.0,
            "LABEL": "Transition Smoothness"
        }
    ],
    "PASSES": [
        {
            "TARGET": "timeBuffer",
            "PERSISTENT": true,
            "FLOAT": true,
            "WIDTH": 1,
            "HEIGHT": 1
        },
        {
            "TARGET": "rotSpeedBuffer",
            "PERSISTENT": true,
            "FLOAT": true,
            "WIDTH": 1,
            "HEIGHT": 1
        },
        {
            "TARGET": "rotationBuffer",
            "PERSISTENT": true,
            "FLOAT": true,
            "WIDTH": 1,
            "HEIGHT": 1
        },
        {
            "TARGET": "finalOutput"
        }
    ]
}*/

// MIT License
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in all
// copies or substantial portions of the Software.
//
// ADDITIONAL ATTRIBUTION REQUIREMENT:
// When using, modifying, or distributing this software, proper acknowledgment
// and credit must be maintained for both the original authors and any
// substantial contributors to derivative works.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
// SOFTWARE.

// Helper function to convert HSV color to RGB
vec3 hsv(float h, float s, float v) {
    vec4 t = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
    vec3 p = abs(fract(vec3(h) + t.xyz) * 6.0 - vec3(t.w));
    return v * mix(vec3(t.x), clamp(p - vec3(t.x), 0.0, 1.0), s);
}

// Helper functions for 3D rotation
mat3 rotationX(float angle) {
    float s = sin(angle);
    float c = cos(angle);
    return mat3(1.0, 0.0, 0.0, 0.0, c, -s, 0.0, s, c);
}

mat3 rotationY(float angle) {
    float s = sin(angle);
    float c = cos(angle);
    return mat3(c, 0.0, s, 0.0, 1.0, 0.0, -s, 0.0, c);
}

mat3 rotationZ(float angle) {
    float s = sin(angle);
    float c = cos(angle);
    return mat3(c, -s, 0.0, s, c, 0.0, 0.0, 0.0, 1.0);
}


void main() {
    // Variable declarations for all passes
    vec4 prevTimeData, prevAngleData, prevSpeedData;
    float accumulatedTime, currentPulsateSpeed, adjustedPulsateSpeed, newTime, effectiveTime;
    vec3 prevAngles, newAngles, effectiveAngles;
    vec3 currentRotSpeed, targetRotSpeed, adjustedRotSpeed, smoothedSpeeds;
    mat3 rotMat;
    float i, g, e, s;
    vec3 p, cameraPos;
    vec2 screenPos;
    float distanceSquared;

    if (PASSINDEX == 0) {
        // --- Pass 0: Time and pulsation speed smoothing ---
        prevTimeData = IMG_NORM_PIXEL(timeBuffer, vec2(0.5, 0.5));
        
        accumulatedTime = prevTimeData.r;
        currentPulsateSpeed = prevTimeData.g;
        
        if (FRAMEINDEX == 0) {
            newTime = 0.0;
            adjustedPulsateSpeed = speed;
        } else {
            adjustedPulsateSpeed = mix(currentPulsateSpeed, speed, min(1.0, TIMEDELTA * transitionSpeed));
            newTime = accumulatedTime + adjustedPulsateSpeed * TIMEDELTA;
        }
        
        gl_FragColor = vec4(newTime, adjustedPulsateSpeed, 0.0, 1.0);

    } else if (PASSINDEX == 1) {
        // --- Pass 1: Rotation SPEED smoothing ---
        prevSpeedData = IMG_NORM_PIXEL(rotSpeedBuffer, vec2(0.5, 0.5));
        currentRotSpeed = prevSpeedData.rgb;
        targetRotSpeed = vec3(rotationSpeedX, rotationSpeedY, rotationSpeedZ);

        if (FRAMEINDEX == 0) {
            adjustedRotSpeed = targetRotSpeed;
        } else {
            adjustedRotSpeed = mix(currentRotSpeed, targetRotSpeed, min(1.0, TIMEDELTA * transitionSpeed));
        }
        gl_FragColor = vec4(adjustedRotSpeed, 1.0);

    } else if (PASSINDEX == 2) {
        // --- Pass 2: Rotation ANGLE accumulation ---
        prevAngleData = IMG_NORM_PIXEL(rotationBuffer, vec2(0.5, 0.5));
        prevAngles = prevAngleData.rgb;
        
        // Get smoothed speeds from the speed buffer
        smoothedSpeeds = IMG_NORM_PIXEL(rotSpeedBuffer, vec2(0.5, 0.5)).rgb;
        
        newAngles = prevAngles + smoothedSpeeds * TIMEDELTA;
        
        gl_FragColor = vec4(newAngles, 1.0);

    } else {
        // --- Pass 3: Render the fractal ---
        effectiveTime = IMG_NORM_PIXEL(timeBuffer, vec2(0.5, 0.5)).r;
        effectiveAngles = IMG_NORM_PIXEL(rotationBuffer, vec2(0.5, 0.5)).rgb;
        
        g = 0.0;
        gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);

        // Build combined rotation matrix from accumulated angles
        rotMat = rotationZ(effectiveAngles.z) * rotationY(effectiveAngles.y) * rotationX(effectiveAngles.x);
        
        // Get camera position from sliders
        cameraPos = vec3(cameraX, cameraY, cameraZ);
        
        // Main raymarching loop - uses user-defined step count
        for (int step = 0; step < raymarchSteps; step++) {
            i = float(step + 1);
            
            screenPos = (gl_FragCoord.xy - 0.5 * RENDERSIZE) / RENDERSIZE.x;
            
            p = vec3(
                screenPos * scale,
                abs(sin(effectiveTime) * 0.15 + 0.8) - g * 1.2
            );

            // Apply camera position offset
            p += cameraPos;

            // Apply buffered rotation
            p = rotMat * p;
            
            s = 1.0;
            
            // Use the user-defined iteration count for fractal detail
            for (int fractal_step = 0; fractal_step < fractalIterations; fractal_step++) {
                distanceSquared = dot(p, p);
                e = max(1.0, 11.0 / distanceSquared);
                s *= e;
                p = vec3(0.0, 3.6, 3.0) - abs(abs(p) * e - vec3(3.0, 2.1, 3.0));
            }
            
            g += mod(length(p.yy), p.y) / s * 0.48;
            
            float hue = 0.01 / g + hue_shift;
            float value = s / 4000.0 * brightness;
            
            gl_FragColor.rgb += hsv(hue, 0.8, value);
        }
    }
}
'''


class IsfImLopyfrac3DOrtho(GlslSource):
    name = 'isf_im_lopyfrac3_d_ortho'
    label = 'IM LOPYFrac3 D Ortho'
    description = 'Complex pulsating fractal with raymarching and smoothed speed/rotation transitions. Camera is now controllable.'
    dialect = 'isf'
    GLSL = GLSL
    params = scene_params({
        "p_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 3.0,
            "default": 0.5,
            "x-ui": {
                "label": "Pulsate Speed",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "speed"
            }
        },
        "rotation_speed_x": {
            "type": "number",
            "minimum": -2.0,
            "maximum": 2.0,
            "default": 0.1,
            "x-ui": {
                "label": "Rotation Speed X",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "rotationSpeedX"
            }
        },
        "rotation_speed_y": {
            "type": "number",
            "minimum": -2.0,
            "maximum": 2.0,
            "default": 0.1,
            "x-ui": {
                "label": "Rotation Speed Y",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "rotationSpeedY"
            }
        },
        "rotation_speed_z": {
            "type": "number",
            "minimum": -2.0,
            "maximum": 2.0,
            "default": 0.1,
            "x-ui": {
                "label": "Rotation Speed Z",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "rotationSpeedZ"
            }
        },
        "camera_x": {
            "type": "number",
            "minimum": -5.0,
            "maximum": 5.0,
            "default": 0.0,
            "x-ui": {
                "label": "Camera X",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "cameraX"
            }
        },
        "camera_y": {
            "type": "number",
            "minimum": -5.0,
            "maximum": 5.0,
            "default": 0.0,
            "x-ui": {
                "label": "Camera Y",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "cameraY"
            }
        },
        "camera_z": {
            "type": "number",
            "minimum": -5.0,
            "maximum": 5.0,
            "default": 0.0,
            "x-ui": {
                "label": "Camera Z",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "cameraZ"
            }
        },
        "scale": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 10.0,
            "default": 5.0,
            "x-ui": {
                "label": "Scale",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "scale"
            }
        },
        "brightness": {
            "type": "number",
            "minimum": 0.1,
            "maximum": 3.0,
            "default": 1.0,
            "x-ui": {
                "label": "Brightness",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "brightness"
            }
        },
        "hue_shift": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Hue shift",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "hue_shift"
            }
        },
        "fractal_iterations": {
            "type": "integer",
            "minimum": 5,
            "maximum": 50,
            "default": 25,
            "x-ui": {
                "label": "Fractal Detail",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "fractalIterations"
            }
        },
        "raymarch_steps": {
            "type": "integer",
            "minimum": 10,
            "maximum": 80,
            "default": 30,
            "x-ui": {
                "label": "Render Depth",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "raymarchSteps"
            }
        },
        "transition_speed": {
            "type": "number",
            "minimum": 0.1,
            "maximum": 10.0,
            "default": 2.0,
            "x-ui": {
                "label": "Transition Smoothness",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "transitionSpeed"
            }
        }
    }, render_scale=0.25)
