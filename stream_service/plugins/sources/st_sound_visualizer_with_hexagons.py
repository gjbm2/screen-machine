"""Sound visualizer with hexagons: Remake for this one https://www.shadertoy.com/view/Xdsfzj A bit refactored with more detailed options and tweaks and no buffers

Source: https://www.shadertoy.com/view/wscfzX
Author: levonchic
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // MIT License (c) bitrate16
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// MIT License (c) bitrate16
// Decorative settings
#define BAR_HEIGHT        0.75        // Bar height (0.0 ~ 1.0)
#define BAR_PERIOD        8           // Bar stepping
#define BAR_WIDTH         8           // Bar height
#define BAR_COLOR         vec4(1.0)   // Bar colors
									  // Triangle color
#define BG_TRIANGLE       vec4(-.065) 
									  // Primary background color
#define BG_COL            vec4(0.065) 
									  // Secondary background color
#define BG_NCOL           vec4(0.005) 
#define BG_GRADIENT_START 0.49        // Gradient effect start value
#define BG_GRADIENT_SCALE 0.45        // Gradient effect scale

// Input settings
#define IN_SOUND          0.25        // Sound probe coord (0.25 - Frequences, 0.75 - Raw wave)
#define IN_TRIANGLE       0.25        // Sound probe for triangle shake (0.25 - Frequences, 0.75 - Raw wave)
#define IN_TRIANGLE_HOR   0.0         // Sound x probe for triangle shake (For x)
#define IN_TRIANGLE_SCALE 0.8         // Scale of triangle shake

// Hexagonal prefences
#define HEX_0_SPEED  1.0 // Speed of Hexagon 0 (Inner)
#define HEX_1_SPEED -0.5 // Speed of Hexagon 1
#define HEX_2_SPEED  0.6 // ...
#define HEX_3_SPEED -0.7 // ...
#define HEX_4_SPEED  0.8 // ...
#define HEX_5_SPEED -0.9 // ...
#define HEX_6_SPEED  1.0 // ...
#define HEX_7_SPEED -1.1 // ...

// Left/Right alignment
float signv(vec2 p1, vec2 p2, vec2 p3) {
    return (p1.x - p3.x) * (p2.y - p3.y) - (p2.x - p3.x) * (p1.y - p3.y);
}

bool inTriangle(vec2 p, vec2 pos, float d) {
    float side2 = d * 0.866025405;
    vec2 v1 = vec2(pos.x,         pos.y + d);
    vec2 v2 = vec2(pos.x - side2, pos.y - d / 2.0);
    vec2 v3 = vec2(pos.x + side2, pos.y - d / 2.0);
    
    bool b1, b2, b3;
    b1 = signv(p, v1, v2) < 0.0;
    b2 = signv(p, v2, v3) < 0.0;
    b3 = signv(p, v3, v1) < 0.0;
    
    return (b1 == b2) && (b2 == b3);
}

bool inHexagon(vec2 p, vec2 pos, float d, float a) {
    float x_ = pos.x - p.x;
    float y_ = pos.y - p.y;
    float s_ = sin(a);
    float c_ = cos(a);
    float dx = abs(x_ * c_ - y_ * s_)/d;
    float dy = abs(x_ * s_ + y_ * c_)/d;
    float ar = 0.25 * sqrt(3.0);
    return (dy <= ar) && (ar * dx + 0.25 * dy <= 0.5 * ar);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 center = iResolution.xy * 0.5;
    float centerLength = length(center);
    float maxResolution = min(iResolution.x, iResolution.y);
    
    // Background: 8 hex + triangle + vingette
    fragColor = vec4(BG_GRADIENT_START - length(fragCoord.xy - center) / centerLength * BG_GRADIENT_SCALE);
    
    // 1/3 of maximal dimension of iResolution
    float d = 0.333 * maxResolution;
    if(inTriangle(fragCoord, center, d * (0.9 + IN_TRIANGLE_SCALE * texture(iChannel0, vec2(IN_TRIANGLE_HOR, IN_TRIANGLE)).x))) 
        fragColor += BG_TRIANGLE;
    if(inHexagon(fragCoord, center, d, iTime * HEX_0_SPEED))
        fragColor += BG_COL;
    else if(inHexagon(fragCoord, center, d * 2.0, iTime * HEX_1_SPEED))
        fragColor += BG_NCOL;
    else if(inHexagon(fragCoord, center, d * 3.0, iTime  * HEX_2_SPEED))
        fragColor += BG_COL;
    else if(inHexagon(fragCoord, center, d * 4.0, iTime * HEX_3_SPEED))
        fragColor += BG_NCOL;
    else if(inHexagon(fragCoord, center, d * 5.0, iTime  * HEX_4_SPEED))
        fragColor += BG_COL;
    else if(inHexagon(fragCoord, center, d * 6.0, iTime * HEX_5_SPEED))
        fragColor += BG_NCOL;
    else if(inHexagon(fragCoord, center, d * 7.0, iTime  * HEX_6_SPEED))
        fragColor += BG_COL;
    else if(inHexagon(fragCoord, center, d * 8.0, iTime * HEX_7_SPEED))
        fragColor += BG_NCOL;
    
    // Locate bars
    int ifragX = int(fragCoord.x) - BAR_WIDTH / 2;
	int ifragXmod = (ifragX + (BAR_PERIOD + BAR_WIDTH)) % (BAR_PERIOD + BAR_WIDTH);
    
    float barXfrag = float(ifragX - ifragXmod);
    float value = texture(iChannel0, vec2(barXfrag / iResolution.x, IN_SOUND)).x;
    float res2y = iResolution.y * 0.5;
        // ifragXmod < BAR_WIDTH && 
    if (ifragXmod < BAR_WIDTH && abs(res2y - fragCoord.y) / res2y < value * BAR_HEIGHT)
        fragColor += BAR_COLOR;
}'''


class StSoundVisualizerWithHexagons(GlslSource):
    name = 'st_sound_visualizer_with_hexagons'
    label = 'Sound visualizer with hexagons'
    description = 'Remake for this one https://www.shadertoy.com/view/Xdsfzj A bit refactored with more detailed options and tweaks and no buffers'
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'music'}
    params = scene_params({}, render_scale=0.5)
