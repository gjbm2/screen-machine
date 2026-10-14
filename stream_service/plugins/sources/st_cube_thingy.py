"""Cube thingy: Spinning cube thing. Using dFdx and dFdy to compute surface normals w.r.t window coordinates.

Source: https://www.shadertoy.com/view/XsdSRN
Author: sillsm
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Copyright Max Sills 2016, licensed under the MIT license.
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Copyright Max Sills 2016, licensed under the MIT license.
// Spinning cube thing, alternative to iq normal func, but rough edges.
//
// Quaternion rotation around axis, of angle, centered at center.
vec3 qRotate(vec3 axis, float angle, vec3 center, vec3 point)
{
axis = normalize(axis);
float x = axis.x * sin(angle / 2.);
float y = axis.y * sin(angle / 2.);
float z = axis.z * sin(angle / 2.);
float w = cos(angle / 2.);

mat3 rotation = mat3(1. - 2.*y*y - 2.*z*z, 2.*(x*y+w*z), 2.*(x*z-w*y),
                     2.*(x*y-w*z), 1. - 2.*x*x - 2.*z*z, 2.*(y*z +w*x),
                     2.*(x*z + w*y), 2.*(y*z-w*x),1. - 2.*x*x - 2.*y*y);
     
return center + (rotation * (point-center));
}

// Rounded box minus a sphere with oscilating radius.
float udBox( vec3 p, vec3 b )
{
  p = qRotate(vec3(0,1.,1.),iTime,vec3(0.), p);
  return max(length(max(abs(p)-b,0.0)) - 1., -1.*(length(p) - 2.-.5*abs(cos(iTime))));
}


vec4 trace(vec3 eye, vec3 viewportxy)				// You -could- do this recursively, but itterative is simpler and safer
{
    vec3 p = vec3(0);
    float t = 0.0;
    for (int i = 0; i < 32; ++i)				// We know what direction the pixel is, and we want to know
    {	
        //   the amount t must be to get to it
        p = viewportxy + (t* normalize(viewportxy-eye));				// Pick a point along the ray r at a distance t from the origin 
        //float distance = distanceFromEdge(p);   // Let d be the new distance from the circle edge, negative or positive
        float dist = udBox(p, vec3(1,1,1));
        //dist = distanceFromEdge(p);
        t += dist * .9;					// Now change the distance factor t by half the distance we currently are from edge
    }
    return vec4(p,t);							
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 uv = fragCoord.xy / iResolution.xy;	// Scale pixel coords to 0..1 range instead
    uv = uv * 2.0 - 1.0;						// Now to the -1 to +1 range
    uv.x *= iResolution.x / iResolution.y;		// Correct for aspect ratio; rescale texcoords horizontally based on it
    
    // assuming viewing plane 
    // Image plane is at (0, 0, -2)
    vec3 viewportxy = vec3(uv , -10.0);		// Direction to the pixel (unit vector, no magnitude)
    vec3 eye = vec3(0, 0, -15);			// Camera origin at 0,0,-3
    
    vec4 tr     = trace(eye, viewportxy);		
    vec3 pos    = normalize(tr.xyz);
    vec3 ref    = vec3(0.,0.,1.);
    float t     = tr.w;
    
    // Normal computation. Nifty and without ref to distance function.
    // But super aliased unless using rounded corners.
    vec3 x = dFdx(tr.xyz);
    vec3 y = dFdy(tr.xyz);
    vec3 normal = normalize(cross(x,y));
    
    normal = qRotate(vec3(0.,1.,1.),iTime,vec3(0.), normal);
    normal = abs(normal);

    fragColor = 5.*(1./t)*vec4(normal,0);;
}'''


class StCubeThingy(GlslSource):
    name = 'st_cube_thingy'
    label = 'Cube thingy'
    description = 'Spinning cube thing. Using dFdx and dFdy to compute surface normals w.r.t window coordinates.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
