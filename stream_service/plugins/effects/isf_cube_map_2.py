"""Cube Map 2: Centered spinning cube with independent flipping for all faces and position sliders.

Source: https://github.com/ProjectileObjects/MiscISFShaders/blob/HEAD/Cube%20Map%202.fs
Author: ProjectileObjects // VIDVOX
License: MIT
license_class: permissive
Imported verbatim from GLSL (ProjectileObjects/MiscISFShaders); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence: repository ProjectileObjects/MiscISFShaders LICENSE (MIT).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
  "CREDIT": "ProjectileObjects // VIDVOX",
  "CATEGORIES": [
    "Cube Map 2.0"
  ],
  "INPUTS": [
    {
      "NAME": "inputImage",
      "TYPE": "image"
    },
    {
      "NAME": "spinX",
      "TYPE": "float",
      "DEFAULT": 0.0,
      "MIN": -3.14,
      "MAX": 3.14
    },
    {
      "NAME": "spinY",
      "TYPE": "float",
      "DEFAULT": 0.0,
      "MIN": -3.14,
      "MAX": 3.14
    },
    {
      "NAME": "spinZ",
      "TYPE": "float",
      "DEFAULT": 0.0,
      "MIN": -3.14,
      "MAX": 3.14
    },
    {
      "NAME": "positionX",
      "TYPE": "float",
      "DEFAULT": 0.0,
      "MIN": -1.0,
      "MAX": 1.0
    },
    {
      "NAME": "positionY",
      "TYPE": "float",
      "DEFAULT": 0.0,
      "MIN": -1.0,
      "MAX": 1.0
    },
    {
      "NAME": "flipFrontH",
      "TYPE": "bool",
      "DEFAULT": true
    },
    {
      "NAME": "flipFrontV",
      "TYPE": "bool",
      "DEFAULT": false
    },
    {
      "NAME": "flipBackH",
      "TYPE": "bool",
      "DEFAULT": false
    },
    {
      "NAME": "flipBackV",
      "TYPE": "bool",
      "DEFAULT": false
    },
    {
      "NAME": "flipLeftH",
      "TYPE": "bool",
      "DEFAULT": true
    },
    {
      "NAME": "flipLeftV",
      "TYPE": "bool",
      "DEFAULT": false
    },
    {
      "NAME": "flipRightH",
      "TYPE": "bool",
      "DEFAULT": false
    },
    {
      "NAME": "flipRightV",
      "TYPE": "bool",
      "DEFAULT": false
    },
    {
      "NAME": "flipTopH",
      "TYPE": "bool",
      "DEFAULT": true
    },
    {
      "NAME": "flipTopV",
      "TYPE": "bool",
      "DEFAULT": true
    },
    {
      "NAME": "flipBottomH",
      "TYPE": "bool",
      "DEFAULT": false
    },
    {
      "NAME": "flipBottomV",
      "TYPE": "bool",
      "DEFAULT": false
    },
    {
      "NAME": "zoom",
      "TYPE": "float",
      "DEFAULT": 1.0,
      "MIN": 0.5,
      "MAX": 5.0
    }
  ],
  "DESCRIPTION": "Centered spinning cube with independent flipping for all faces and position sliders."
}*/

#ifdef GL_ES
precision mediump float;
#endif

// Function to check ray-box intersection
bool intersects(vec3 ro, vec3 rd, vec3 box_center, float box_size, out float t_intersection) {
    vec3 t1 = (box_center - vec3(box_size) - ro) / rd;
    vec3 t2 = (box_center + vec3(box_size) - ro) / rd;
    vec3 t_min = min(t1, t2);
    vec3 t_max = max(t1, t2);
    float t_near = max(t_min.x, max(t_min.y, t_min.z));
    float t_far = min(t_max.x, min(t_max.y, t_max.z));
    if (t_near > t_far || t_far < 0.0) return false;
    t_intersection = t_near;
    return true;
}

// Camera matrix
mat3 camera(vec3 e, vec3 la) {
    vec3 roll = vec3(0, 1, 0);
    vec3 f = normalize(la - e);
    vec3 r = normalize(cross(roll, f));
    vec3 u = normalize(cross(f, r));
    return mat3(r, u, f);
}

// Rotation matrix for X, Y, and Z spins
mat3 rotationMatrix(float spinX, float spinY, float spinZ) {
    mat3 rotX = mat3(
        1.0, 0.0, 0.0,
        0.0, cos(spinX), -sin(spinX),
        0.0, sin(spinX), cos(spinX)
    );

    mat3 rotY = mat3(
        cos(spinY), 0.0, sin(spinY),
        0.0, 1.0, 0.0,
        -sin(spinY), 0.0, cos(spinY)
    );

    mat3 rotZ = mat3(
        cos(spinZ), -sin(spinZ), 0.0,
        sin(spinZ), cos(spinZ), 0.0,
        0.0, 0.0, 1.0
    );

    return rotZ * rotY * rotX;
}

// Determine the texture coordinates based on the hit point and flipping per face
vec2 getTexCoords(vec3 hitPoint, vec3 box_center, float box_size) {
    vec3 localPoint = (hitPoint - box_center) / box_size;

    vec2 texCoord;
    if (abs(localPoint.x) > 0.99) {
        texCoord = vec2(localPoint.z, localPoint.y) * 0.5 + 0.5;
        if (localPoint.x > 0.0) {
            // Right face
            if (flipRightH) texCoord.x = 1.0 - texCoord.x;
            if (flipRightV) texCoord.y = 1.0 - texCoord.y;
        } else {
            // Left face
            if (flipLeftH) texCoord.x = 1.0 - texCoord.x;
            if (flipLeftV) texCoord.y = 1.0 - texCoord.y;
        }
    } else if (abs(localPoint.y) > 0.99) {
        texCoord = vec2(localPoint.x, localPoint.z) * 0.5 + 0.5;
        if (localPoint.y > 0.0) {
            // Top face
            if (flipTopH) texCoord.x = 1.0 - texCoord.x;
            if (flipTopV) texCoord.y = 1.0 - texCoord.y;
        } else {
            // Bottom face
            if (flipBottomH) texCoord.x = 1.0 - texCoord.x;
            if (flipBottomV) texCoord.y = 1.0 - texCoord.y;
        }
    } else {
        texCoord = vec2(localPoint.x, localPoint.y) * 0.5 + 0.5;
        if (localPoint.z > 0.0) {
            // Front face
            if (flipFrontH) texCoord.x = 1.0 - texCoord.x;
            if (flipFrontV) texCoord.y = 1.0 - texCoord.y;
        } else {
            // Back face
            if (flipBackH) texCoord.x = 1.0 - texCoord.x;
            if (flipBackV) texCoord.y = 1.0 - texCoord.y;
        }
    }

    return texCoord;
}

void main(void) {
    const float INFINITY = 1e3;
    float t_intersection = INFINITY;

    vec2 uv = (2.0 * gl_FragCoord.xy - RENDERSIZE) / min(RENDERSIZE.x, RENDERSIZE.y);
    uv /= zoom;

    // Add position offsets
    uv.x -= positionX; // Corrected for slider behavior
    uv.y += positionY;

    // Ray origin and direction
    vec3 ro = vec3(0.0, 0.0, 3.0); // Fixed camera position
    vec3 rd = normalize(vec3(uv, -1.0)); // Ray direction

    // Rotate the cube around its own center
    vec3 cube_center = vec3(0.0, 0.0, 0.0); // Cube is centered at origin
    float cube_size = 1.0;

    mat3 cubeRotation = rotationMatrix(spinX, spinY, spinZ); // Rotation matrix
    ro = cubeRotation * (ro - cube_center) + cube_center; // Rotate ray origin around cube
    rd = cubeRotation * rd; // Rotate ray direction

    vec4 color = vec4(0.0);

    // Check for intersection with the cube
    if (intersects(ro, rd, cube_center, cube_size, t_intersection)) {
        vec3 hitPoint = ro + rd * t_intersection;
        vec2 texCoord = getTexCoords(hitPoint, cube_center, cube_size);
        color = IMG_NORM_PIXEL(inputImage, texCoord);
    }

    gl_FragColor = color;
}'''


class IsfCubeMap2(GlslEffect):
    name = 'isf_cube_map_2'
    label = 'Cube Map 2'
    description = 'Centered spinning cube with independent flipping for all faces and position sliders.'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'stylise'
    params = effect_params({
        "spin_x": {
            "type": "number",
            "minimum": -3.14,
            "maximum": 3.14,
            "default": 0.0,
            "x-ui": {
                "label": "Spin X",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "spinX"
            }
        },
        "spin_y": {
            "type": "number",
            "minimum": -3.14,
            "maximum": 3.14,
            "default": 0.0,
            "x-ui": {
                "label": "Spin Y",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "spinY"
            }
        },
        "spin_z": {
            "type": "number",
            "minimum": -3.14,
            "maximum": 3.14,
            "default": 0.0,
            "x-ui": {
                "label": "Spin Z",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "spinZ"
            }
        },
        "position_x": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Position X",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "positionX"
            }
        },
        "position_y": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "default": 0.0,
            "x-ui": {
                "label": "Position Y",
                "step": 0.01,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "positionY"
            }
        },
        "flip_front_h": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Flip Front H"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flipFrontH"
            }
        },
        "flip_front_v": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Flip Front V"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flipFrontV"
            }
        },
        "flip_back_h": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Flip Back H"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flipBackH"
            }
        },
        "flip_back_v": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Flip Back V"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flipBackV"
            }
        },
        "flip_left_h": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Flip Left H"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flipLeftH"
            }
        },
        "flip_left_v": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Flip Left V"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flipLeftV"
            }
        },
        "flip_right_h": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Flip Right H"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flipRightH"
            }
        },
        "flip_right_v": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Flip Right V"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flipRightV"
            }
        },
        "flip_top_h": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Flip Top H"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flipTopH"
            }
        },
        "flip_top_v": {
            "type": "boolean",
            "default": True,
            "x-ui": {
                "label": "Flip Top V"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flipTopV"
            }
        },
        "flip_bottom_h": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Flip Bottom H"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flipBottomH"
            }
        },
        "flip_bottom_v": {
            "type": "boolean",
            "default": False,
            "x-ui": {
                "label": "Flip Bottom V"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "flipBottomV"
            }
        },
        "zoom": {
            "type": "number",
            "minimum": 0.5,
            "maximum": 5.0,
            "default": 1.0,
            "x-ui": {
                "label": "Zoom",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "zoom"
            }
        }
    })
