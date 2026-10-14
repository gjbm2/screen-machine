"""SDF Quadratic Bézier Shape: Signed distance function for a shape made out of quadratic Bézier curves. In the tradition of iq's series such as this one:...

Source: https://www.shadertoy.com/view/ftdGDB
Author: DrNoob
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Author: Thomas Stehle
// Title: SDF Quadratic Bézier Shape
//
// The MIT License
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
//
// Signed distance function for a shape made out of quadratic
// Bézier curves in the tradition of iq's series such as this
// one: https://www.shadertoy.com/view/MlKcDD.
//
// The only real contribution of this shader is that it postpones
// the call to the costly Bézier SDF up until the point it has
// identified the closest segment of the control polygon.
// So instead of iterating over all Bézier curves, we iterate
// over the line segments of the control polygon and identify the
// closest segment. We then call the Bézier SDF for this segment
// only. This approach is correct since quadratic Bézier curves
// are always contained in the triangle formed by its three
// control points.

// Constants
const int CAPACITY = 32; // Control polygon capacity
const float INF   = 1.0 / 0.0;
const float SQRT3 = 1.732050807568877;

// Cross-product of two 2D vectors
float cross2(in vec2 a, in vec2 b) {
    return a.x*b.y - a.y*b.x;
}

// Clamp a value to [0, 1]
float saturate(in float a) {
    return clamp(a, 0.0, 1.0);
}
vec3 saturate(in vec3 a) {
    return clamp(a, 0.0, 1.0);
}

// Minimum of the absolute of two values
float abs_min(float a, float b) {
    return abs(a) < abs(b) ? a : b;
}

// SDF for a line segment
float sdf_line(in vec2 p, in vec2 a, in vec2 b) {
    float h = saturate(dot(p - a, b - a) /
                       dot(b - a, b - a));
    return length(p - a - h * (b - a));
}

// Like the SDF for a line but partitioning space into positive and negative
float sdf_line_partition(in vec2 p, in vec2 a, in vec2 b) {
    vec2 ba = b - a;
    vec2 pa = p - a;
    float h = saturate(dot(pa, ba) / dot(ba, ba));
    vec2 k = pa - h * ba;
    vec2 n = vec2(ba.y, -ba.x);
    return (dot(k,n) >= 0.0) ? length(k) : -length(k);
}

// Signed distance to a quadratic Bézier curve
// Mostly identical to https://www.shadertoy.com/view/MlKcDD
// with some additions to combat degenerate cases.
float sdf_bezier(in vec2 pos, in vec2 A, in vec2 B, in vec2 C) {
    const float EPSILON = 1e-3;
    const float ONE_THIRD = 1.0 / 3.0;

    // Handle cases where points coincide
    bool abEqual = all(equal(A, B));
    bool bcEqual = all(equal(B, C));
    bool acEqual = all(equal(A, C));
    
    if (abEqual && bcEqual) {
        return distance(pos, A);
    } else if (abEqual || acEqual) {
        return sdf_line_partition(pos, B, C);
    } else if (bcEqual) {
        return sdf_line_partition(pos, A, C);
    }
    
    // Handle colinear points
    if (abs(dot(normalize(B - A), normalize(C - B)) - 1.0) < EPSILON) {
        return sdf_line_partition(pos, A, C);
    }
    
    vec2 a = B - A;
    vec2 b = A - 2.0*B + C;
    vec2 c = a * 2.0;
    vec2 d = A - pos;

    float kk = 1.0 / dot(b,b);
    float kx = kk * dot(a,b);
    float ky = kk * (2.0*dot(a,a)+dot(d,b)) * ONE_THIRD;
    float kz = kk * dot(d,a);

    float res = 0.0;
    float sgn = 0.0;

    float p = ky - kx*kx;
    float p3 = p*p*p;
    float q = kx*(2.0*kx*kx - 3.0*ky) + kz;
    float h = q*q + 4.0*p3;

    if (h >= 0.0) {
        // One root
        h = sqrt(h);
        vec2 x = 0.5 * (vec2(h, -h) - q);
        vec2 uv = sign(x) * pow(abs(x), vec2(ONE_THIRD));
        float t = saturate(uv.x + uv.y - kx) + EPSILON;
        vec2 q = d + (c + b*t) * t;
        res = dot(q, q);
        sgn = cross2(c + 2.0*b*t, q);
    } else {
        // Three roots
        float z = sqrt(-p);
        float v = acos(q/(p*z*2.0)) * ONE_THIRD;
        float m = cos(v);
        float n = sin(v) * SQRT3;
        vec3 t = saturate(vec3(m+m,-n-m,n-m)*z-kx) + EPSILON;
        vec2 qx = d + (c+b*t.x)*t.x;
        float dx = dot(qx, qx);
        float sx = cross2(c+2.0*b*t.x, qx);
        vec2 qy = d + (c+b*t.y)*t.y;
        float dy = dot(qy, qy);
        float sy = cross2(c+2.0*b*t.y, qy);
        res = (dx < dy) ? dx : dy;
        sgn = (dx < dy) ? sx : sy;
    }
    
    return sign(sgn) * sqrt(res);
}

// Signed distance to a segment of a control polygon
float sdf_control_segment(in vec2 p, in vec2 A, in vec2 B, in vec2 C) {
    return abs_min(sdf_line(p, A, B), sdf_line(p, B, C));
}

// Signed distance to a control polygon
// Identifies and returns distance to the closest segment.
float sdf_control_polygon(in vec2 p, in vec2 controlPoly[CAPACITY], in int controlPolySize, out vec2 closest[3]) {
    // Cycle through segments and track the closest
    float d = INF;
    float ds = 0.0;

    // First n-2 segments
    vec2 c = 0.5 * (controlPoly[0] + controlPoly[1]);
    vec2 prev = c;
    for (int i = 1; i < controlPolySize - 1; ++i) {
        prev = c;
        c = 0.5 * (controlPoly[i] + controlPoly[i+1]);
        ds = sdf_control_segment(p, prev, controlPoly[i], c);
        if (abs(ds) < abs(d)) {
            closest[0] = prev;
            closest[1] = controlPoly[i];
            closest[2] = c;
            d = ds;
        }
    }

    // Last-but-one segment
    prev = c;
    c = 0.5 * (controlPoly[controlPolySize-1] + controlPoly[0]);
    ds = sdf_control_segment(p, prev, controlPoly[controlPolySize-1], c);
    if (abs(ds) < abs(d)) {
        closest[0] = prev;
        closest[1] = controlPoly[controlPolySize-1];
        closest[2] = c;
        d = ds;
    }

    // Last segment
    prev = c;
    c = 0.5 * (controlPoly[0] + controlPoly[1]);
    ds = sdf_control_segment(p, prev, controlPoly[0], c);
    if (abs(ds) < abs(d)) {
        closest[0] = prev;
        closest[1] = controlPoly[0];
        closest[2] = c;
        d = ds;
    }
    
    // Return distance
    return d;
}

// Signed distance to a quadratic Bezier shape made from a given control polygon
float sdf_bezier_shape(in vec2 p, in vec2 controlPoly[CAPACITY], in int controlPolySize) {
    // Determine closest segment in control polygon
    vec2 closest[3];
    sdf_control_polygon(p, controlPoly, controlPolySize, closest);

    // Refine by determining actual distance to curve of closest segment
    return sdf_bezier(p, closest[0], closest[1], closest[2]);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord)
{
    // Pixel coordinates
    vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
    
    // Mouse coordinates
    vec2 m = (2.0*iMouse.xy-iResolution.xy)/iResolution.y;
    
    // Control polygon animation
    float off = sin(iTime);
    const float R = 0.7071;
    
    // Control polygon
    const int SIZE = 8;
    vec2 controlPoints[CAPACITY];
    controlPoints[0] = vec2(-0.75, -0.75);
    controlPoints[1] = vec2( 0.0 , -0.75) + vec2(0.0, -off) * R;
    controlPoints[2] = vec2( 0.75, -0.75);
    controlPoints[3] = vec2( 0.75,  0.0 ) + vec2(off, 0.0) * R;
    controlPoints[4] = vec2( 0.75,  0.75);
    controlPoints[5] = vec2( 0.0 ,  0.75) + vec2(0.0, off) * R;
    controlPoints[6] = vec2(-0.75,  0.75);
    controlPoints[7] = vec2(-0.75,  0.0 ) + vec2(-off, 0.0) * R;

    // Distance to shape
    float d = sdf_bezier_shape(p, controlPoints, SIZE);
    
    // Distance field
    vec3 col = vec3(1.0) - vec3(0.1,0.4,0.7)*mix(sign(d),1.0,-1.0);
	col *= 1.0 - exp(-4.0*abs(d));
	col *= 0.8 + 0.2*cos(140.0*d);
    
    // Shape
	col = mix(col, vec3(1.0), 1.0-smoothstep(0.0,0.015,abs(d)));
    
    // Control polygon
    if (cos(iTime) > 0.0) {
        vec2 closest[3];
        d = sdf_control_polygon(p, controlPoints, SIZE, closest);
        d = min(d, length(p-closest[1])-0.02);
        col = mix(col, vec3(1,0,0), 1.0-smoothstep(0.0,0.007,d));
    }
    
    // Distance to mouse position as circle
    if (iMouse.z > 0.001) {
        d = sdf_bezier_shape(m, controlPoints, SIZE); 
        col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, abs(length(p-m)-abs(d))-0.0025));
        col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, length(p-m)-0.015));
    }
    
    // Output to screen
    fragColor = vec4(col, 1.0);
}'''


class StSdfQuadraticBZierShape(GlslSource):
    name = 'st_sdf_quadratic_b_zier_shape'
    label = 'SDF Quadratic Bézier Shape'
    description = "Signed distance function for a shape made out of quadratic Bézier curves. In the tradition of iq's series such as this one:..."
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
