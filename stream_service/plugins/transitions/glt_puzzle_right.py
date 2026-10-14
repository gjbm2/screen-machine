"""Puzzle Right: The image splits into a grid of tiles that flip one by one to the incoming image

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/PuzzleRight.glsl
Author: JustKirillS
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: JustKirillS
// License: MIT
// Description: The image splits into a grid of tiles that flip one by one to the incoming image
// Tags: grid, tiles, flip
// Ported from https://gist.github.com/JustKirillS/714f095318834f4d2375de872c53af1e

uniform ivec2 size; // = ivec2(4, 4)
uniform float pause; // = 0.1
uniform float dividerWidth; // = 0.005

float rand(vec2 co) {
  return fract(sin(dot(co, vec2(12.9898, 78.233))) * 43758.5453);
}

float getDelta(vec2 p) {
  vec2 rectangleSize = 1.0 / vec2(size);
  vec2 rectanglePos = floor(vec2(size) * p);
  float top = rectangleSize.y * (rectanglePos.y + 1.0);
  float bottom = rectangleSize.y * rectanglePos.y;
  float left = rectangleSize.x * rectanglePos.x;
  float right = rectangleSize.x * (rectanglePos.x + 1.0);
  float minX = min(abs(p.x - left), abs(p.x - right));
  float minY = min(abs(p.y - top), abs(p.y - bottom));
  return min(minX, minY);
}

vec4 transition(vec2 uv) {
  if (progress < pause) {
    float currentProg = progress / pause;
    float a = 1.0;
    if (getDelta(uv) < dividerWidth) { a = 1.0 - currentProg; }
    return mix(vec4(0.0, 0.0, 0.0, 1.0), getFromColor(uv), a);
  } else if (progress < 1.0 - pause) {
    if (getDelta(uv) < dividerWidth) {
      return vec4(0.0, 0.0, 0.0, 1.0);
    }
    float currentProg = (progress - pause) / (1.0 - pause * 2.0);
    vec2 rectanglePos = floor(vec2(size) * uv);
    float r = rand(rectanglePos) - 0.1;
    float cp = smoothstep(0.0, 1.0 - r, currentProg);
    float rectangleSize = 1.0 / float(size.x);
    float delta = rectanglePos.x * rectangleSize;
    float offset = rectangleSize / 2.0 + delta;
    vec2 p = uv;
    p.x = (p.x - offset) / abs(cp - 0.5) * 0.5 + offset;
    vec4 a = getFromColor(p);
    vec4 b = getToColor(p);
    float s = step(abs(float(size.x) * (uv.x - delta) - 0.5), abs(cp - 0.5));
    return vec4(mix(b, a, step(cp, 0.5)).rgb * s, 1.0);
  } else {
    float currentProg = (progress - 1.0 + pause) / pause;
    float a = 1.0;
    if (getDelta(uv) < dividerWidth) { a = currentProg; }
    return mix(vec4(0.0, 0.0, 0.0, 1.0), getToColor(uv), a);
  }
}
'''


class GltPuzzleRight(GlslTransition):
    name = 'glt_puzzle_right'
    label = 'Puzzle Right'
    description = 'The image splits into a grid of tiles that flip one by one to the incoming image'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "size_x": {
            "type": "integer",
            "minimum": 0,
            "maximum": 16,
            "default": 4,
            "x-ui": {
                "label": "Size x",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "size",
                "c": 0
            }
        },
        "size_y": {
            "type": "integer",
            "minimum": 0,
            "maximum": 16,
            "default": 4,
            "x-ui": {
                "label": "Size y",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "size",
                "c": 1
            }
        },
        "pause": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.1,
            "x-ui": {
                "label": "Pause",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "pause"
            }
        },
        "divider_width": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.005,
            "x-ui": {
                "label": "Divider Width",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "dividerWidth"
            }
        }
    }
