"""Physarum Polycephalum Simulation: Voronoi particle tracking to simulate dynamics of slime mold, converted from <https://www.shadertoy.com/view/tlKGDh>

Source: https://github.com/nwhetsell/isf-shaders/blob/HEAD/Physarum%20Polycephalum%20Simulation.fs
Author: Mykhailo Moroz <https://www.shadertoy.com/user/michael0884>
License: Prosperity-3.0.0
license_class: noncommercial
Imported verbatim from GLSL (nwhetsell/isf-shaders); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Uses LYGIA (Prosperity licence, noncommercial).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Filter",
        "Generator"
    ],
    "CREDIT": "Mykhailo Moroz <https://www.shadertoy.com/user/michael0884>",
    "DESCRIPTION": "Voronoi particle tracking to simulate dynamics of slime mold, converted from <https://www.shadertoy.com/view/tlKGDh>",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "NAME": "inputImageAmount",
            "LABEL": "Input image amount",
            "TYPE": "float",
            "DEFAULT": 0,
            "MIN": 0,
            "MAX": 1
        },
        {
            "NAME": "decayInputImage",
            "LABEL": "Decay input image",
            "TYPE": "bool",
            "DEFAULT": true
        },
        {
            "NAME": "restart",
            "LABEL": "Restart",
            "TYPE": "event"
        },
        {
            "NAME": "mouse",
            "TYPE": "point2D",
            "DEFAULT": [0.8, 0.05],
            "MIN": [0, 0],
            "MAX": [1, 1]
        },
        {
            "NAME": "simulationSpeed",
            "LABEL": "Simulation speed",
            "TYPE": "float",
            "DEFAULT": 0.25,
            "MAX": 1,
            "MIN": 0
        },
        {
            "NAME": "trailSize",
            "LABEL": "Trail size",
            "TYPE": "float",
            "DEFAULT": 1.4,
            "MAX": 10,
            "MIN": 0
        },
        {
            "NAME": "trailDecay",
            "LABEL": "Trail decay",
            "TYPE": "float",
            "DEFAULT": 0.15,
            "MAX": 10,
            "MIN": 0
        },
        {
            "NAME": "particleSpeed",
            "LABEL": "Particle speed",
            "TYPE": "float",
            "DEFAULT": 6,
            "MAX": 100,
            "MIN": 0
        },
        {
            "NAME": "particleSpeedRandomness",
            "LABEL": "Particle speed randomness",
            "TYPE": "float",
            "DEFAULT": 0.1,
            "MAX": 10,
            "MIN": 0
        },
        {
            "NAME": "particleMaxSearchRadius",
            "LABEL": "Particle max. search radius",
            "TYPE": "float",
            "DEFAULT": 5,
            "MAX": 10,
            "MIN": 1
        },
        {
            "NAME": "particleCloneFactor",
            "LABEL": "Particle clone factor",
            "TYPE": "float",
            "DEFAULT": 1,
            "MAX": 10,
            "MIN": 0
        },
        {
            "NAME": "particleCloneDistance",
            "LABEL": "Particle clone distance",
            "TYPE": "float",
            "DEFAULT": 10,
            "MAX": 100,
            "MIN": 0
        },
        {
            "NAME": "sensorDistance",
            "LABEL": "Sensor distance",
            "TYPE": "float",
            "DEFAULT": 10,
            "MAX": 100,
            "MIN": 0
        },
        {
            "NAME": "sensorStrength",
            "LABEL": "Sensor strength",
            "TYPE": "float",
            "DEFAULT": 10,
            "MAX": 100,
            "MIN": 0
        },
        {
            "NAME": "sensorAngle",
            "LABEL": "Sensor angle (radians)",
            "TYPE": "float",
            "DEFAULT": 0.3,
            "MAX": 6,
            "MIN": 0
        },
        {
            "NAME": "sensedDirectionFactor",
            "LABEL": "Sensed direction factor",
            "TYPE": "float",
            "DEFAULT": 3,
            "MAX": 10,
            "MIN": 1
        },
        {
            "NAME": "radiusFactor",
            "LABEL": "Radius factor",
            "TYPE": "float",
            "DEFAULT": 0,
            "MAX": 10,
            "MIN": 0
        },
        {
            "NAME": "blurProportion",
            "LABEL": "Blur proportion",
            "TYPE": "float",
            "DEFAULT": 0.9,
            "MAX": 1,
            "MIN": 0
        }
    ],
    "ISFVSN": "2",
    "PASSES": [
        {
            "TARGET": "particles",
            "PERSISTENT": true,
            "FLOAT": true
        },
        {
            "TARGET": "trails",
            "PERSISTENT": true,
            "FLOAT": true
        },
        {
            "TARGET": "diffuseTrails",
            "PERSISTENT": true,
            "FLOAT": true
        },
        {

        }
    ]
}*/
// #define ISF_EDITOR_WEBSITE
// These are also interesting defaults on the ISF website:
//   simulationSpeed: 0.19
//   trailSize: 6
//   trailDecay: 0.51
//   particleSpeed: 24.9
//   particleSpeedRandomness: 0
//   particleCloneFactor: 2.7
//   sensorDistance: 8.49
//   sensorStrength: 11.84
//   sensorAngle: 3.4
//   angleDifferenceFactor: 3
#define RANDOM_HIGHER_RANGE 
#define RANDOM_SINLESS 
/*
contributors: ["Patricio Gonzalez Vivo", "David Hoskins", "Inigo Quilez"]
description: Pass a value and get some random normalize value between 0 and 1
use: float random[2|3](<float|vec2|vec3> value)
options:
    - RANDOM_HIGHER_RANGE: for working with a range over 0 and 1
    - RANDOM_SINLESS: Use sin-less random, which tolerates bigger values before producing pattern. From https://www.shadertoy.com/view/4djSRW
    - RANDOM_SCALE: by default this scale if for number with a big range. For producing good random between 0 and 1 use bigger range
examples:
    - /shaders/generative_random.frag
license:
    - MIT License (MIT) Copyright 2014, David Hoskins
*/
#define RANDOM_SCALE vec4(.1031, .1030, .0973, .1099)
#define FNC_RANDOM 
float random(in float x) {
    x = fract(x * RANDOM_SCALE.x);
    x *= x + 33.33;
    x *= x + x;
    return fract(x);
}
float random(in vec2 st) {
    vec3 p3 = fract(vec3(st.xyx) * RANDOM_SCALE.xyz);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
float random(in vec3 pos) {
    pos = fract(pos * RANDOM_SCALE.xyz);
    pos += dot(pos, pos.zyx + 31.32);
    return fract((pos.x + pos.y) * pos.z);
}
float random(in vec4 pos) {
    pos = fract(pos * RANDOM_SCALE);
    pos += dot(pos, pos.wzxy + 33.33);
    return fract((pos.x + pos.y) * (pos.z + pos.w));
}
vec2 random2(float p) {
    vec3 p3 = fract(vec3(p) * RANDOM_SCALE.xyz);
    p3 += dot(p3, p3.yzx + 19.19);
    return fract((p3.xx + p3.yz) * p3.zy);
}
vec2 random2(vec2 p) {
    vec3 p3 = fract(p.xyx * RANDOM_SCALE.xyz);
    p3 += dot(p3, p3.yzx + 19.19);
    return fract((p3.xx + p3.yz) * p3.zy);
}
vec2 random2(vec3 p3) {
    p3 = fract(p3 * RANDOM_SCALE.xyz);
    p3 += dot(p3, p3.yzx + 19.19);
    return fract((p3.xx + p3.yz) * p3.zy);
}
vec3 random3(float p) {
    vec3 p3 = fract(vec3(p) * RANDOM_SCALE.xyz);
    p3 += dot(p3, p3.yzx + 19.19);
    return fract((p3.xxy + p3.yzz) * p3.zyx);
}
vec3 random3(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * RANDOM_SCALE.xyz);
    p3 += dot(p3, p3.yxz + 19.19);
    return fract((p3.xxy + p3.yzz) * p3.zyx);
}
vec3 random3(vec3 p) {
    p = fract(p * RANDOM_SCALE.xyz);
    p += dot(p, p.yxz + 19.19);
    return fract((p.xxy + p.yzz) * p.zyx);
}
vec4 random4(float p) {
    vec4 p4 = fract(p * RANDOM_SCALE);
    p4 += dot(p4, p4.wzxy + 19.19);
    return fract((p4.xxyz + p4.yzzw) * p4.zywx);
}
vec4 random4(vec2 p) {
    vec4 p4 = fract(p.xyxy * RANDOM_SCALE);
    p4 += dot(p4, p4.wzxy + 19.19);
    return fract((p4.xxyz + p4.yzzw) * p4.zywx);
}
vec4 random4(vec3 p) {
    vec4 p4 = fract(p.xyzx * RANDOM_SCALE);
    p4 += dot(p4, p4.wzxy + 19.19);
    return fract((p4.xxyz + p4.yzzw) * p4.zywx);
}
vec4 random4(vec4 p4) {
    p4 = fract(p4 * RANDOM_SCALE);
    p4 += dot(p4, p4.wzxy + 19.19);
    return fract((p4.xxyz + p4.yzzw) * p4.zywx);
}
/*
contributors: Patricio Gonzalez Vivo
description: some useful math constants
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define EIGHTH_PI 0.39269908169
#define QTR_PI 0.78539816339
#define HALF_PI 1.5707963267948966192313216916398
#define PI 3.1415926535897932384626433832795
#define TWO_PI 6.2831853071795864769252867665590
#define TAU 6.2831853071795864769252867665590
#define INV_PI 0.31830988618379067153776752674503
#define INV_SQRT_TAU 0.39894228040143267793994605993439
#define SQRT_HALF_PI 1.25331413732
#define PHI 1.618033988749894848204586834
#define EPSILON 0.0000001
#define GOLDEN_RATIO 1.6180339887
#define GOLDEN_RATIO_CONJUGATE 0.61803398875
#define GOLDEN_ANGLE 2.39996323
#define DEG2RAD (PI / 180.0)
#define RAD2DEG (180.0 / PI)
/*
contributors: Patricio Gonzalez Vivo
description: gaussian coefficient
use: <vec4|vec3|vec2|float> gaussian(<float> sigma, <vec4|vec3|vec2|float> d)
examples:
    - https://raw.githubusercontent.com/patriciogonzalezvivo/lygia_examples/main/math_gaussian.frag
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_GAUSSIAN 
float gaussian(float d, float s) { return exp(-(d*d) / (2.0 * s*s)); }
float gaussian( vec2 d, float s) { return exp(-( d.x*d.x + d.y*d.y) / (2.0 * s*s)); }
float gaussian( vec3 d, float s) { return exp(-( d.x*d.x + d.y*d.y + d.z*d.z ) / (2.0 * s*s)); }
float gaussian( vec4 d, float s) { return exp(-( d.x*d.x + d.y*d.y + d.z*d.z + d.w*d.w ) / (2.0 * s*s)); }
/*
contributors: [Ivan Dianov, Shadi El Hajj]
description: polar to cartesian conversion.
use: polar2cart(<vec2> polar)
*/
#define FNC_POLAR2CART 
vec2 polar2cart(in vec2 polar) {
    return vec2(cos(polar.x), sin(polar.x)) * polar.y;
}
// https://mathworld.wolfram.com/SphericalCoordinates.html
vec3 polar2cart( in float r, in float phi, in float theta) {
    float x = r * cos(theta) * sin(phi);
    float y = r * sin(theta) * sin(phi);
    float z = r * cos(phi);
    return vec3(x, y, z);
}

#define SQRT1_2 0.7071067811865475244008443621048

// In the Shadertoy shader, values less than 0 and greater than 1 are written to
// an image buffer. This is impossible without floating-point buffers; ISF
// shaders clamp 8-bit buffers to be between 0 and 1. Consequently, unless
// floating-point buffers are available, we must scale particle data to be
// between 0 and 1 when writing them to an image, and unscale particle data when
// reading from an image.
#define SCALE_PARTICLE(PARTICLE) 
#define UNSCALE_PARTICLE(PARTICLE) 
//
// Shadertoy Common
//
// This should be an input variable, but the shader doesn’t initialize correctly
// unless this is a #define.
#define INITIAL_PARTICLE_DENSITY 2.
// This is the `loop` function in Buffer A of the original Shadertoy shader.
vec2 wrapToRenderSize(vec2 position)
{
    return mod(position, RENDERSIZE);
}
void main()
{
    vec2 position = gl_FragCoord.xy;
    if (PASSINDEX == 0) // Shadertoy Buffer A
    {
        float scaledSensorDistance = sensorDistance;
        float scaledSensorStrength = sensorStrength;
        if (length(mouse.xy) > 0.) {
            scaledSensorDistance *= mouse.x;
            scaledSensorStrength *= mouse.y;
        }
        // This pixel value
        vec4 particle = IMG_PIXEL(particles, position);
        UNSCALE_PARTICLE(particle);
        // Check neighbours
        vec2 halfSize = 0.5 * RENDERSIZE;
        for (float radius = 1.; radius <= particleMaxSearchRadius; radius += 1.) {
            // This would be *much* easier to do with an array initializer:
            //    vec2 positionOffsets[] = vec2[](vec2(-radius, 0), vec2(radius, 0), vec2(0, -radius), vec2(0, radius));
            // and the array length member function:
            //    https://www.khronos.org/opengl/wiki/Data_Type_(GLSL)#Arrays
            //    https://www.khronos.org/opengl/wiki/Data_Type_(GLSL)#Array_constructors
            const int positionOffsetCount = 4;
            vec2 positionOffsets[positionOffsetCount];
            positionOffsets[0] = vec2(-radius, 0);
            positionOffsets[1] = vec2( radius, 0);
            positionOffsets[2] = vec2( 0, -radius);
            positionOffsets[3] = vec2( 0, radius);
            for (int i = 0; i < positionOffsetCount; i++) {
                vec4 neighbor = IMG_PIXEL(particles, wrapToRenderSize(position + positionOffsets[i]));
                UNSCALE_PARTICLE(neighbor);
                // Check if the stored neighbouring particle is closer to this position.
                float neighborDistance = length(wrapToRenderSize(neighbor.xy - position + halfSize) - halfSize);
                float particleDistance = length(wrapToRenderSize(particle.xy - position + halfSize) - halfSize);
                if (neighborDistance < particleDistance) {
                    particle = neighbor;
                }
            }
        }
        particle.xy = wrapToRenderSize(particle.xy);
        // Cell cloning
        if (length(particle.xy - position) > particleCloneDistance) {
            particle.xy += particleCloneFactor * (random2(position) - 0.5);
        }
        // Sensors
        vec2 sensorCounterclockwisePosition = particle.xy + polar2cart(vec2(particle.z + sensorAngle, scaledSensorDistance));
        vec2 sensorClockwisePosition = particle.xy + polar2cart(vec2(particle.z - sensorAngle, scaledSensorDistance));
        // It’s unclear whether IMG_NORM_PIXEL is doing any interpolation.
        float sensedDirection = IMG_NORM_PIXEL(trails, sensorCounterclockwisePosition / RENDERSIZE).x -
                                IMG_NORM_PIXEL(trails, sensorClockwisePosition / RENDERSIZE).x;
        particle.z += simulationSpeed * scaledSensorStrength * tanh(sensedDirectionFactor * sensedDirection);
        vec2 particleVelocity = polar2cart(vec2(particle.z, particleSpeed)) + particleSpeedRandomness * (random2(particle.xy + TIME) - 0.5);
        // Update the particle
        particle.xy += simulationSpeed * particleVelocity;
        particle.xy = wrapToRenderSize(particle.xy);
        if (radiusFactor > 0.) {
            float minDimension = radiusFactor * min(halfSize.x, halfSize.y);
            if (length(halfSize - particle.xy) > minDimension) {
                particle.xy = minDimension * normalize(particle.xy - halfSize) + halfSize;
                particle.z += PI;
            }
        }
        if (FRAMEINDEX < 1 || restart) {
            particle.xy = vec2(
                INITIAL_PARTICLE_DENSITY * round(position.x / INITIAL_PARTICLE_DENSITY),
                INITIAL_PARTICLE_DENSITY * round(position.y / INITIAL_PARTICLE_DENSITY)
            );
            particle.zw = random2(particle.xy) - 0.5;
        }
        SCALE_PARTICLE(particle);
        gl_FragColor = particle;
    }
    else if (PASSINDEX == 1) // Shadertoy Buffer B
    {
        vec4 trail = IMG_PIXEL(trails, position);
        // Diffusion
        // This is the `Laplace` function in the Common tab of the original
        // Shadertoy shader. In the jit.gl.isf Max object (available with the
        // ISF package), it seems that IMG_PIXEL cannot be used outside the GLSL
        // main function, so inline the `Laplace` function here.
        vec3 dx = vec3(-1, 0, 1);
        vec4 laplacian = IMG_PIXEL(trails, position + dx.xy) +
                         IMG_PIXEL(trails, position + dx.yx) +
                         IMG_PIXEL(trails, position + dx.zy) +
                         IMG_PIXEL(trails, position + dx.yz) -
                         4. * IMG_PIXEL(trails, position);
        trail += simulationSpeed * laplacian;
        vec4 particle = IMG_PIXEL(particles, position);
        UNSCALE_PARTICLE(particle);
        // Pheromone depositing
        float depositRate = gaussian(position - particle.xy, trailSize * SQRT1_2);
        if (decayInputImage) {
            depositRate += inputImageAmount * length(IMG_PIXEL(inputImage, position).rgb);
        }
        trail += simulationSpeed * depositRate;
        // Pheromone decay
        trail -= simulationSpeed * trailDecay * trail;
        if (FRAMEINDEX < 1 || restart) {
            trail = vec4(0);
        }
        if (decayInputImage) {
            gl_FragColor = trail;
        } else {
            gl_FragColor = mix(trail, IMG_PIXEL(inputImage, position), inputImageAmount);
        }
    }
    else if (PASSINDEX == 2) // Shadertoy Buffer C
    {
        gl_FragColor = blurProportion * IMG_PIXEL(diffuseTrails, position) + (1. - blurProportion) * IMG_PIXEL(trails, position);
        if (FRAMEINDEX < 1 || restart) {
            gl_FragColor = vec4(0);
        }
    }
    else // Shadertoy Image
    {
        vec4 diffuseTrail = 2.5 * IMG_PIXEL(diffuseTrails, position);
        gl_FragColor = vec4(sin(diffuseTrail.xyz * vec3(1, 1.2, 1.5)), 1);
    }
}
'''


class IsfPhysarumPolycephalumSimulation(GlslEffect):
    name = 'isf_physarum_polycephalum_simulation'
    label = 'Physarum Polycephalum Simulation'
    description = 'Voronoi particle tracking to simulate dynamics of slime mold, converted from <https://www.shadertoy.com/view/tlKGDh>'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'stylise'
    scale = 0.35
    events = ('restart',)
    params = effect_params({
        "input_image_amount": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Input image amount",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "inputImageAmount"
            }
        },
        "decay_input_image": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Decay input image"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "decayInputImage"
            }
        },
        "mouse_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.8,
            "x-ui": {
                "label": "Mouse x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mouse",
                "c": 0
            }
        },
        "mouse_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.05,
            "x-ui": {
                "label": "Mouse y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "mouse",
                "c": 1
            }
        },
        "simulation_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.25,
            "x-ui": {
                "label": "Simulation speed",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "simulationSpeed"
            }
        },
        "trail_size": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.4,
            "x-ui": {
                "label": "Trail size",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "trailSize"
            }
        },
        "trail_decay": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 0.15,
            "x-ui": {
                "label": "Trail decay",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "trailDecay"
            }
        },
        "particle_speed": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
            "default": 6.0,
            "x-ui": {
                "label": "Particle speed",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "particleSpeed"
            }
        },
        "particle_speed_randomness": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 0.1,
            "x-ui": {
                "label": "Particle speed randomness",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "particleSpeedRandomness"
            }
        },
        "particle_max_search_radius": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 10.0,
            "default": 5.0,
            "x-ui": {
                "label": "Particle max. search radius",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "particleMaxSearchRadius"
            }
        },
        "particle_clone_factor": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 1.0,
            "x-ui": {
                "label": "Particle clone factor",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "particleCloneFactor"
            }
        },
        "particle_clone_distance": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
            "default": 10.0,
            "x-ui": {
                "label": "Particle clone distance",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "particleCloneDistance"
            }
        },
        "sensor_distance": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
            "default": 10.0,
            "x-ui": {
                "label": "Sensor distance",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "sensorDistance"
            }
        },
        "sensor_strength": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
            "default": 10.0,
            "x-ui": {
                "label": "Sensor strength",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "sensorStrength"
            }
        },
        "sensor_angle": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 6.0,
            "default": 0.3,
            "x-ui": {
                "label": "Sensor angle (radians)",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "sensorAngle"
            }
        },
        "sensed_direction_factor": {
            "type": "number",
            "minimum": 1.0,
            "maximum": 10.0,
            "default": 3.0,
            "x-ui": {
                "label": "Sensed direction factor",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "sensedDirectionFactor"
            }
        },
        "radius_factor": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "default": 0.0,
            "x-ui": {
                "label": "Radius factor",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "radiusFactor"
            }
        },
        "blur_proportion": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.9,
            "x-ui": {
                "label": "Blur proportion",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "blurProportion"
            }
        }
    })
