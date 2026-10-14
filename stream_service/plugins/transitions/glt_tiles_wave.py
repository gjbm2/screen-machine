"""Tiles Wave: The incoming image is revealed tile by tile in a diagonal wave

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/TilesWave.glsl
Author: numb3r23
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: numb3r23
// License: MIT
// Description: The incoming image is revealed tile by tile in a diagonal wave
// Tags: tiles, wave, wipe
// Ported from https://gist.github.com/numb3r23/169781bb76f310e2bfde

// @param Number of tiles (columns, rows)
uniform ivec2 tileCount; // = ivec2(8, 8)
uniform bool flipX; // = true
uniform bool flipY; // = false

vec4 transition(vec2 uv) {
  vec2 tileSize = 1.0 / vec2(tileCount);
  vec2 posInTile = fract(uv * vec2(tileCount));
  vec2 tileNum = floor(uv * vec2(tileCount));
  float countTiles = float(tileCount.x * tileCount.y);

  // Diagonal wave from bottom-left to top-right
  float offset = (tileNum.y + tileNum.x * float(tileCount.y)) / countTiles;
  float timeOffset = clamp((progress - offset) * countTiles, 0.0, 0.5);
  float sinTime = 1.0 - abs(cos(fract(timeOffset) * 3.1415926));

  vec2 texC = posInTile;

  if (sinTime <= 0.5) {
    if (flipX) {
      if (texC.x < sinTime || texC.x > 1.0 - sinTime)
        return getFromColor(uv);
      texC.x = texC.x < 0.5
        ? (texC.x - sinTime) * 0.5 / (0.5 - sinTime)
        : (texC.x - 0.5) * 0.5 / (0.5 - sinTime) + 0.5;
    }
    if (flipY) {
      if (texC.y < sinTime || texC.y > 1.0 - sinTime)
        return getFromColor(uv);
      texC.y = texC.y < 0.5
        ? (texC.y - sinTime) * 0.5 / (0.5 - sinTime)
        : (texC.y - 0.5) * 0.5 / (0.5 - sinTime) + 0.5;
    }
    vec2 globalUV = tileNum * tileSize + texC * tileSize;
    return getFromColor(globalUV);
  } else {
    if (flipX) {
      if (texC.x > sinTime || texC.x < 1.0 - sinTime)
        return getToColor(uv);
      texC.x = texC.x < 0.5
        ? (texC.x - sinTime) * 0.5 / (0.5 - sinTime)
        : (texC.x - 0.5) * 0.5 / (0.5 - sinTime) + 0.5;
      texC.x = 1.0 - texC.x;
    }
    if (flipY) {
      if (texC.y > sinTime || texC.y < 1.0 - sinTime)
        return getToColor(uv);
      texC.y = texC.y < 0.5
        ? (texC.y - sinTime) * 0.5 / (0.5 - sinTime)
        : (texC.y - 0.5) * 0.5 / (0.5 - sinTime) + 0.5;
      texC.y = 1.0 - texC.y;
    }
    vec2 globalUV = tileNum * tileSize + texC * tileSize;
    return getToColor(globalUV);
  }
}
'''


class GltTilesWave(GlslTransition):
    name = 'glt_tiles_wave'
    label = 'Tiles Wave'
    description = 'The incoming image is revealed tile by tile in a diagonal wave'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "tile_count_x": {
            "type": "integer",
            "minimum": 0,
            "maximum": 32,
            "default": 8,
            "x-ui": {
                "label": "Number of tiles (columns, rows) x",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "tileCount",
                "c": 0
            }
        },
        "tile_count_y": {
            "type": "integer",
            "minimum": 0,
            "maximum": 32,
            "default": 8,
            "x-ui": {
                "label": "Number of tiles (columns, rows) y",
                "step": 1,
                "widget": "slider"
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "tileCount",
                "c": 1
            }
        },
        "flip_x": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Flip X"
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "flipX"
            }
        },
        "flip_y": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Flip Y"
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "flipY"
            }
        }
    }
