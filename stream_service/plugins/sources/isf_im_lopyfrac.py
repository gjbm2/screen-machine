"""IM LOPYFrac: Complex pulsating fractal structure with raymarching and smoothed speed transitions.

Source: https://github.com/bareimage/ISF/blob/HEAD/Release.4/IM-LOPYFrac.fs
Author: @YoheiNishitsuji twigl.app shader, ISF 2.0 by dot2dot
License: MIT
license_class: permissive
Imported verbatim from GLSL (bareimage/ISF); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*{
    "DESCRIPTION": "Complex pulsating fractal structure with raymarching and smoothed speed transitions.",
    "CREDIT": "@YoheiNishitsuji twigl.app shader, ISF 2.0 by dot2dot",
    "ISFVSN": "2.0",
    "CATEGORIES": [
        "Generator",
        "Fractal"
    ],
    "INPUTS": [
        {
            "NAME": "speed",
            "TYPE": "float",
            "DEFAULT": 1.0,
            "MIN": 0.0,
            "MAX": 3.0
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

vec3 hsv(float h, float s, float v) {
    vec4 t = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
    vec3 p = abs(fract(vec3(h) + t.xyz) * 6.0 - vec3(t.w));
    return v * mix(vec3(t.x), clamp(p - vec3(t.x), 0.0, 1.0), s);
}

void main() {
    // Pass-specific variables
    vec4 prevTimeData;
    float accumulatedTime, currentSpeed, adjustedSpeed, newTime, effectiveTime;

    // Rendering variables
    float i, g, e, s;
    vec3 p;
    vec2 screenPos;
    float distanceSquared;

    if (PASSINDEX == 0) {
        // Pass 0: Update and store the accumulated time with smoothing
        prevTimeData = IMG_NORM_PIXEL(timeBuffer, vec2(0.5, 0.5));
        
        accumulatedTime = prevTimeData.r;
        currentSpeed = prevTimeData.g;
        
        if (FRAMEINDEX == 0) {
            newTime = 0.0;
            adjustedSpeed = speed;
        } else {
            adjustedSpeed = mix(currentSpeed, speed, min(1.0, TIMEDELTA * transitionSpeed));
            newTime = accumulatedTime + adjustedSpeed * TIMEDELTA;
        }
        
        gl_FragColor = vec4(newTime, adjustedSpeed, 0.0, 1.0);

    } else {
        // Final Pass: Render the fractal using the smoothed time
        effectiveTime = IMG_NORM_PIXEL(timeBuffer, vec2(0.5, 0.5)).r;
        
        g = 0.0;
        gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);
        
        for (int step = 0; step < 20; step++) {
            i = float(step + 1);
            
            screenPos = (gl_FragCoord.xy - 0.5 * RENDERSIZE) / RENDERSIZE.x;
            
            p = vec3(
                screenPos * scale,
                abs(sin(effectiveTime) * 0.15 + 0.8) - g * 1.2
            );
            
            s = 1.0;
            
            for (int fractal_step = 0; fractal_step < 25; fractal_step++) {
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
}'''


class IsfImLopyfrac(GlslSource):
    name = 'isf_im_lopyfrac'
    label = 'IM LOPYFrac'
    description = 'Complex pulsating fractal structure with raymarching and smoothed speed transitions.'
    dialect = 'isf'
    GLSL = GLSL
    params = scene_params({
        "p_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 3.0,
            "default": 1.0,
            "x-ui": {
                "label": "Speed",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "speed"
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
