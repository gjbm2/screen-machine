"""Negative light: My very first shader, basic tests aside. Not much to say, it’s just basic stuff bundled in one shader. Comments and criticism are welcomed, for I am an...

Source: https://www.shadertoy.com/view/sldSRj
Author: Mots_Doux
License: public-domain
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // It means you can do anything you want, it’s pretty much Public Domain
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// This shader is licensed under the Creative Commons Zero 1.0 License
// https://creativecommons.org/publicdomain/zero/1.0/legalcode
// It means you can do anything you want, it’s pretty much Public Domain

#define TAU 6.28318530718

float square(float x) {
    return x * x;
}

// function to draw the grid
// has a lot of parameters but allows avoiding some code duplication
vec4 grid(vec2 uv, float cellSize, vec3 color1, vec3 color2, float ratio) {
    if ((mod(uv.x * ratio, cellSize) <= cellSize/2.0 && mod(uv.y, cellSize) <= cellSize/2.0) || (mod(uv.x * ratio, cellSize) >= cellSize/2.0 && mod(uv.y, cellSize) >= cellSize/2.0)) {
        return vec4(color1, 1.0);
    } else {
        return vec4(color2, 1.0);
    }
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // coordinates of the pixel
    vec2 uv = fragCoord/iResolution.xy;
    
    // duration of the animation in seconds
    float duration = 5.0;
    
    // colors of the grid
    vec3 color1 = vec3(1.0, 1.0, 1.0);
    vec3 color2 = vec3(0.0, 0.436, 1.0);

    // ratio of the display surface so we have a proper circle and not an ellipse
    float ratio = iResolution.x / iResolution.y;
    
    // center of the circle, moves with time
    // this is a very simple movement only for me to explore the iTime parameter and the cos function
    vec2 center = vec2(cos(TAU / duration * iTime) * 0.2 + 0.5, cos(TAU / duration * iTime) * 0.2 + 0.5);
    
    // radius of our circle
    float radius = 0.3;
    
    // size of each cell of the grid
    float cellSize = 0.5;
    
    // circle equation, true if the pixel is inside the circle
    // (pixel.x - center.x)^2 + (pixel.y - center.y)^2 <= radius^2
    // pixels in the circle get an inverted color
    /*if (square(uv.x * ratio - center.x) + square(uv.y - center.y) <= square(radius)) {
        fragColor = 1.0 - grid(uv, cellSize, color1, color2, ratio);
    } else {
        fragColor = grid(uv, cellSize, color1, color2, ratio);
    }*/
    
    // change suggested by hamtarodeluxe, replace the commented lines above + removes aliasing
    vec4 col = grid(uv, cellSize, color1, color2, ratio);
    float squareRadius = square(radius);
    // returns smooth Hermite interpolation between 0 and 1 when
    // squareRadius < square(uv.x * ratio - center.x) + square(uv.y - center.y) < squareRadius + 0.005
    float t = smoothstep(squareRadius, squareRadius + 0.005, square(uv.x * ratio - center.x) + square(uv.y - center.y));
    // mix is simply a lerp function
    fragColor = mix(1.-col, col, t);
    
}'''


class StNegativeLight(GlslSource):
    name = 'st_negative_light'
    label = 'Negative light'
    description = 'My very first shader, basic tests aside. Not much to say, it’s just basic stuff bundled in one shader. Comments and criticism are welcomed, for I am an...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
