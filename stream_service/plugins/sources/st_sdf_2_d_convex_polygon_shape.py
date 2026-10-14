"""SDF 2 D Convex Polygon Shape: This variant of a generic (convex) polygon shader uses "point-in-polygon" testing if a point is at a right-hand turn away from all the line segments of the...

Source: https://www.shadertoy.com/view/wdffWH
Author: RiggsDK
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2020 Anders Riggelsen
// Based uppon Inigo Quilez original code
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


struct Result{
    float dist;     //Regular SDF distance
    float side;     //Which side of the line segment the point is (-1,0,1)
};
   
Result udSegment( in vec2 p, in vec2 a, in vec2 b )
{
    Result res;
    //All this is basically Inigo's regular line SDF function - but store it in 'dist' instead: 
    vec2 ba = b-a;
    vec2 pa = p-a;
    float h = clamp( dot(pa,ba)/dot(ba,ba), 0.0, 1.0 );
    res.dist = length(pa-h*ba);
    //Is the movement (a->b->p) a righthand turn? (cross product)
    res.side = sign( (b.x - a.x)*(p.y - a.y) - (b.y - a.y)*(p.x - a.x) ); 
    return res;
}

vec2 jiggle(vec2 p)
{
    return p + vec2(sin(iTime*p.y), cos(iTime*p.x))*0.25;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
    
    //List of points. All could be put into a loop if need be.
    //Jiggled for your viewing pleasure.
    vec2 pA = jiggle(vec2(-0.8,  0.7));
    vec2 pB = jiggle(vec2( 1.0,  0.8));
    vec2 pC = jiggle(vec2( 1.0, -0.4));
    vec2 pD = jiggle(vec2( 0.0, -1.2));
    vec2 pE = jiggle(vec2(-1.0, -0.6));

    //Get the SDF distances for the line segments along with the 'side':
    Result rA = udSegment( p, pA, pB);
    Result rB = udSegment( p, pB, pC );
    Result rC = udSegment( p, pC, pD );
    Result rD = udSegment( p, pD, pE );
    Result rE = udSegment( p, pE, pA );
    
    //Union of all distances
    float d =  rA.dist;
    d = min(d, rB.dist);
    d = min(d, rC.dist);
    d = min(d, rD.dist);
    d = min(d, rE.dist);
    //At this point all we have is a SDF of an infinitely thin shape.
    //To make it "solid" we have to give all the points on the inside a negative distance.
    
    //But which side is the point on?
    //If the "side" is negative for *all* N segments the sum will be -N.
    //Adding N and subtracting a small value ]0-1[ before getting the sign
    //of this sum makes it either 1 (outside of shape) or -1 (inside of shape)
    d = d * sign(rA.side + rB.side + rC.side + rD.side + rE.side + 5.0 - 0.5);
        
    //Inigo's pretty colors:
    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
    col *= 1.0 - exp(-3.0*abs(d));
    col *= 0.8 + 0.2*cos(120.0*d);
    col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.015,abs(d)) );
    fragColor = vec4(col,1.0);
}'''


class StSdf2DConvexPolygonShape(GlslSource):
    name = 'st_sdf_2_d_convex_polygon_shape'
    label = 'SDF 2 D Convex Polygon Shape'
    description = 'This variant of a generic (convex) polygon shader uses "point-in-polygon" testing if a point is at a right-hand turn away from all the line segments of the...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
