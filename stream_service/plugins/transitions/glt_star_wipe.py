"""Star Wipe: A growing star reveals the incoming image

Source: https://github.com/gl-transitions/gl-transitions/blob/b2dfdcfbec079af4a05306d32b9366aa495c5155/transitions/StarWipe.glsl
Author: Ben Lucas
License: MIT
license_class: permissive
Imported verbatim from GLSL (gl-transitions); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
"""
from stream_service.plugins._lib.glsl import GlslTransition


GLSL = r'''// Author: Ben Lucas
// License: MIT
// Description: A growing star reveals the incoming image
// Tags: wipe, shape, star
#define PI 3.141592653589793
#define STAR_ANGLE 1.2566370614359172

// @param Thickness of the star's border
uniform float border_thickness;// = 0.01
uniform float star_rotation;// = 0.75
// @color Color of the star's border
uniform vec4 border_color; // = vec4(1.0)
// @param Center of the star
uniform vec2 star_center;// = vec2(0.5)

vec2 rotate(vec2 v, float theta) {
    float cosTheta = cos(theta);
    float sinTheta = sin(theta);

    return vec2(
        cosTheta * v.x - sinTheta * v.y,
        sinTheta * v.x + cosTheta * v.y
    );
}

bool inStar(vec2 uv, vec2 center, float radius){
  vec2 uv_centered = uv - center;
  uv_centered = rotate(uv_centered, star_rotation * STAR_ANGLE);
  float theta = atan(uv_centered.y, uv_centered.x) + PI;

  vec2 uv_rotated = rotate(uv_centered, -STAR_ANGLE * (floor(theta / STAR_ANGLE) + 0.5));

  float slope = 0.3;
  if(uv_rotated.y > 0.0){
      return (radius + uv_rotated.x * slope > uv_rotated.y);
  } else {
     return (-radius - uv_rotated.x * slope < uv_rotated.y);
  }
}

vec4 transition (vec2 uv) {
  float progressScaled = (2.0 * border_thickness + 1.0) * progress - border_thickness;
  if(inStar(uv, star_center, progressScaled)){
    return getToColor(uv);
  } else if(inStar(uv, star_center, progressScaled+border_thickness)){
    return border_color;
  } else {
    return getFromColor(uv);
  }
}
'''


class GltStarWipe(GlslTransition):
    name = 'glt_star_wipe'
    label = 'Star Wipe'
    description = 'A growing star reveals the incoming image'
    dialect = 'glt'
    GLSL = GLSL
    params = {
        "border_thickness": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.01,
            "x-ui": {
                "label": "Thickness of the star's border",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "border_thickness"
            }
        },
        "star_rotation": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.75,
            "x-ui": {
                "label": "Star rotation",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "star_rotation"
            }
        },
        "border_color": {
            "type": "string",
            "format": "color",
            "default": "#ffffff",
            "x-ui": {
                "label": "Color of the star's border",
                "widget": "color"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "border_color",
                "color": 4,
                "alpha": 1.0
            }
        },
        "star_center_x": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Center of the star x",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "star_center",
                "c": 0
            }
        },
        "star_center_y": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "x-ui": {
                "label": "Center of the star y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "star_center",
                "c": 1
            }
        }
    }
