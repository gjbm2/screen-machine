"""Chessboard: A chessboard pattern of squares reveals the incoming image

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/chessboard.glsl
Author: lql
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: lql
// License: MIT
// Description: A chessboard pattern of squares reveals the incoming image
// Tags: grid, squares, pattern

// @param Number of squares per row
uniform float grid_num; // = 10.0

vec4 transition(vec2 uv) {
    vec2 st = uv * grid_num;
    vec2 idx = floor(st);
    vec2 grid = fract(st);

    vec4 a = getFromColor(uv);
    vec4 b = getToColor(uv);

    float checker = mod(idx.x + idx.y, 2.0);
    float mixFactor;

    if (progress <= 0.5) {
        mixFactor = (checker > 0.5) ? step(grid.x, progress * 2.0) : 0.0;
    } else {
        mixFactor = (checker < 0.5) ? step(grid.x, (progress - 0.5) * 2.0) : 1.0;
    }

    return mix(a, b, mixFactor);
}
'''


class GltChessboard(GlslTransition):
    name = 'glt_chessboard'
    label = 'Chessboard'
    description = 'A chessboard pattern of squares reveals the incoming image'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "grid_num": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 20.0,
            "default": 10.0,
            "x-ui": {
                "label": "Number of squares per row",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "grid_num"
            }
        }
    }
