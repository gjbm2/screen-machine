"""Oscilloscope analysis luminance: Oscilloscope luminance analysis, converted from <https://www.shadertoy.com/view/Nttyz4>

Source: https://github.com/nwhetsell/isf-shaders/blob/HEAD/oscilloscope%20analysis%20luminance.fs
Author: Fabrice Neyret <https://www.shadertoy.com/user/FabriceNeyret2>
License: CC-BY-NC-SA-3.0
license_class: noncommercial
Imported verbatim from GLSL (nwhetsell/isf-shaders); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Shadertoy-derived: Shadertoy's default licence applies.
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Filter"
    ],
    "CREDIT": "Fabrice Neyret <https://www.shadertoy.com/user/FabriceNeyret2>",
    "DESCRIPTION": "Oscilloscope luminance analysis, converted from <https://www.shadertoy.com/view/Nttyz4>",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "NAME": "gridDensity",
            "LABEL": "Grid density",
            "TYPE": "float",
            "DEFAULT": 3,
            "MAX": 20,
            "MIN": 0
        }
    ],
    "ISFVSN": "2"
}*/
float gridStep(float p)
{
    return smoothstep(1., 0., abs(p) / fwidth(p));
}
void main()
{
    vec2 normalizedCoordinate = gl_FragCoord.xy / RENDERSIZE;
    vec2 gridSize = RENDERSIZE / exp2(gridDensity);
    vec2 gridCoordinate = round(normalizedCoordinate * gridSize) / gridSize;
    gridSize = gridSize * (normalizedCoordinate - gridCoordinate) + 0.5;
    gl_FragColor = vec4(0);
    vec4 pixelColor;
    pixelColor = IMG_NORM_PIXEL(inputImage, vec2(normalizedCoordinate.x, gridCoordinate.y)); // horizontal RGB profiles
    gl_FragColor = mix(gl_FragColor, vec4(1), gridStep(gridSize.y - length(pixelColor.rgb) / sqrt(3.)));
    pixelColor = IMG_NORM_PIXEL(inputImage, vec2(gridCoordinate.x, normalizedCoordinate.y)); // vertical RGB profiles
    gl_FragColor = mix(gl_FragColor, vec4(1), gridStep(gridSize.x - length(pixelColor.rgb) / sqrt(3.)));
    gl_FragColor = sqrt(gl_FragColor);
}
'''


class IsfOscilloscopeAnalysisLuminance(GlslEffect):
    name = 'isf_oscilloscope_analysis_luminance'
    label = 'Oscilloscope analysis luminance'
    description = 'Oscilloscope luminance analysis, converted from <https://www.shadertoy.com/view/Nttyz4>'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'stylise'
    params = effect_params({
        "grid_density": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 20.0,
            "default": 3.0,
            "x-ui": {
                "label": "Grid density",
                "step": 0.1,
                "widget": "slider"
            },
            "x-tier": "regional",
            "x-glsl": {
                "u": "gridDensity"
            }
        }
    })
