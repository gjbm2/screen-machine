"""Orange Tunnel: A simple shader that generates a striped tunnel. Used for tutorials and videos about Wire, our real-time shader editing software. https://moditone.com/wire

Source: https://www.shadertoy.com/view/Wd2Szh
Author: moditone
License: WTFPL
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License: Do What the Fuck You Want to Public License (http://www.wtfpl.net/)
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License: Do What the Fuck You Want to Public License (http://www.wtfpl.net/)

#define PI 3.14159265359

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // Compute the center of the tunnel
	vec2 center = (fragCoord.xy / iResolution.xy) * 2.0 - 1.0;
    
    // Fix aspect ratio
    center.x *= iResolution.x / iResolution.y;
    
    // Animate the center of the tunnel
    center.x += sin(iTime) * 0.2;
    center.y += cos(iTime) * 0.15;
    
    // Generate the tunnel stripes
    float a = 1. / length(center) + atan(center.x, center.y) / PI * 1.0 + iTime;
    float b = sin(a * PI * 8.) * dot(center, center) * iResolution.y / 150. - 0.3;
    float c = b * sin(length(center) - .1);
	
    fragColor = mix(vec4(0, 0, 0, 1), vec4(0.5, 0.25, 0, 1), c);
}'''


class StOrangeTunnel(GlslSource):
    name = 'st_orange_tunnel'
    label = 'Orange Tunnel'
    description = 'A simple shader that generates a striped tunnel. Used for tutorials and videos about Wire, our real-time shader editing software. https://moditone.com/wire'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
