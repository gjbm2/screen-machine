"""Drink The Water: Drink the Water

Source: https://www.shadertoy.com/view/ftKGzm
Author: Jabo
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// https://iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm
float sdCircle( in vec2 p, in float r ) 
{
    return length(p)-r;
}

// https://iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm
float sdTriangle( in vec2 p )
{ 
    const float k = sqrt(3.0);
    p.x = abs(p.x) - 1.0; p.y = p.y + 1.0/k;
    if( p.x+k*p.y>0.0 ) p = vec2(p.x-k*p.y,-k*p.x-p.y)/2.0;
    p.x -= clamp( p.x, -2.0, 0.0 );
    return -length(p)*sign(p.y);
}

// https://iquilezles.org/www/articles/distfunctions/distfunctions.htm
float opSmoothSubtraction( float d1, float d2, float k )
{
    float h = clamp( 0.5 - 0.5*(d2+d1)/k, 0.0, 1.0 );
    return mix( d2, -d1, h ) + k*h*(1.0-h);
}

// modified https://www.shadertoy.com/view/ldBGDc
float spiral(vec2 m, float t) {
	float r = pow(length(m) / 0.75, 3.5) * 0.75;
	float a = atan(m.x, m.y);
    float v = sin(48.*(sqrt(r)-0.0625*a-.05*t));
	return clamp(v,0.,1.);
}

// adapted for webgl https://www.ronja-tutorials.com/post/041-hsv-colorspace/#rgb-to-hsv-conversion
vec3 hue2rgb(float hue) {
    hue = fract(hue); //only use fractional part of hue, making it loop
    float r =      abs(hue * 6. - 3.) - 1.; //red
    float g = 2. - abs(hue * 6. - 2.);    //green
    float b = 2. - abs(hue * 6. - 4.);  //blue
    vec3 rgb = vec3(r,g,b); //combine components
    rgb = clamp(rgb, 0., 1.); //saturate
    return rgb;
}
vec3 hsv2rgb(vec3 hsv)
{
    vec3 rgb = hue2rgb(hsv.x); //apply hue
    rgb = mix(vec3(1.0), rgb, hsv.y); //apply saturation
    rgb = rgb * hsv.z; //apply value
    return rgb;
}

vec3 scene(vec2 fragCoord, float iTime) {
    vec3 col = vec3(0.5);
    vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
    vec2 m = (2.0*iMouse.xy-iResolution.xy)/iResolution.y;
    
    float radius = 0.8 + 0.1*cos(iTime)*cos(0.1*iTime);

	float d = sdCircle(p, radius);
    
    // stripes
    float distortion = 6.0*cos(69.0*d/2.0 + cos(iTime))*clamp(sin(4.+0.09*iTime),0.,1.) * (d + .5) / iResolution.y;
    float fraction = clamp((1.0 - p.y)/2.0+distortion, 0.01, 1.0);
    float stripe = clamp(floor(fraction * 6.0) / 6.0, 0.0, 1.0);
      // upper 3: mix(-0.06, 0.65, stripe)
      // lower 3: mix(-0.1, 0.91, stripe)
    float stripeHue1 = stripe < 0.5 ? mix(-0.06, 0.65, stripe) : mix(-0.1, 0.91, stripe);
    stripe += 1./6.;
    float stripeHue2 = stripe < 0.5 ? mix(-0.06, 0.65, stripe) : mix(-0.1, 0.91, stripe);
    float stripeHue = mix(stripeHue1, stripeHue2, smoothstep(stripe - 0.003, stripe + 0.003, fraction - 0.0005));
    float stripeSaturation = mix(0.95, 0.45, pow(clamp(sin(stripe * 6. + 0.4*iTime),0.,1.),24.)*clamp(2.*sin(5.+ 0.031*iTime),0.,1.));
    col = hsv2rgb(vec3(clamp(stripeHue,0.001, 0.75), stripeSaturation, 0.95));
    if(iMouse.z > 0.001) {
        // old method has no smoothstep to reduce aliasing
        float stripe = (floor(clamp((1.0 - p.y)/2.0+distortion, 0.01, 1.0) * 6.0) / 6.0);
      // upper 3: mix(-0.06, 0.65, stripe)
      // lower 3: mix(-0.1, 0.91, stripe)
        float stripeHue = stripe < 0.5 ? mix(-0.06, 0.65, stripe) : mix(-0.1, 0.91, stripe);
        col = hsv2rgb(vec3(clamp(stripeHue,0.001, 0.75), 0.95, 0.95));
    }
    
    // vingette glow
    col = mix(col, col * 0.95 + 0.2 * exp(-8.0*abs(d)), 0.5 + 0.5*cos(0.7*iTime));
    // ripples
	col = mix(col, col * cos(69.0*d + 4.2*cos(iTime)), clamp(0.1*cos(3.0+0.05*iTime),0.0,1.0));
    // spiral
    if ( d < 0.0 )
    {
        vec2 uv = vec2(0.9, 0.5) - fragCoord.xy / iResolution.y;
        float s = spiral(uv, iTime);
        s = smoothstep(0.0, 0.05, s);
        col = vec3(s);
        // droplet
        vec2 p2 = p * (0.95 + 0.05 * sin(0.7*iTime));
        float d2 = min(sdCircle(p2+vec2(0.0,0.1), 0.3), sdTriangle((p2-vec2(0.0,0.2))*3.65)) - 0.01;
        if (d2 < 0.005 )
        {
            col = mix(hsv2rgb(vec3(0.60, 0.95, 0.95)), col, smoothstep(0.0, 0.005, d2));
            // reflection
            float d3 = opSmoothSubtraction(
                sdCircle(p2+vec2(0.2,-0.1), 0.46),
                sdCircle(p2+vec2(0.0,0.1), 0.24),
                0.02
            );
            d3 *= 1.2;
            if(d3 < 0.005)
            {
                col = mix(vec3(1), col, smoothstep(0.0, 0.005, d3));
            }
            
        }
    }
    // outer border
    col = mix( col, vec3(0.0), 1.0-smoothstep(0.0,0.005, abs(d) - 0.02) );
    return col;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    float abberation = 0.06*sin(0.033*iTime);
    vec3 col = vec3(0);
    // yes temporal chromatic abberation makes no sense, but we don't have to make sense
	col += scene(fragCoord, iTime - abberation) * vec3(.8,.1,.1);
    col += scene(fragCoord, iTime             ) * vec3(.1,.8,.1);
    col += scene(fragCoord, iTime + abberation) * vec3(.1,.1,.8);

	fragColor = vec4(col,1.0);
}

// c9558532c93019c667e3fc5e14532d21'''


class StDrinkTheWater(GlslSource):
    name = 'st_drink_the_water'
    label = 'Drink The Water'
    description = 'Drink the Water'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
