"""Distance field tutorial part 2: The second part of the distance field raymarching tutorial, where we focus on point lighting with a simplistic shading model.

Source: https://www.shadertoy.com/view/4tf3zM
Author: sebastien
License: BSD-3-Clause
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: * License: BSD License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*
 * # Raymarched distance-field renderer tutorial
 * # Part 2: Lighting
 *
 * ```
 * Author:  SÃ©bastien Pierre   
 * Website: http://sebastienpierre.ca
 * Twitter: @ssebastien
 * License: BSD License
 * ```
 *
 * This is the second part of the Raymarched Distance-Field Renderer tutorial.
 * In the first part <https://www.shadertoy.com/view/XtXGzM>, 
 * we've seen the `map`, `interesect` and `main` functions that constitute the base
 * of the renderer. We've created a sphere
 * with a very simple lighting model. 
 *
 * In this part of the tutorial, we're going going to add light
 * sources of different colors and "shade" our sphere so that it does not
 * look grey anymore.
 *
 * Have a look at [CFT], and espectially the `GetObjectLighting` if you'd like
 * to see how to implement more visual properties on top of this model.
 *
 * References:
 *
 * - [PART1] https://www.shadertoy.com/view/XtXGzM
 * - [LIGT] http://www1.cs.columbia.edu/~cs4162/slides/lecture16.pdf
 * - [CFT] https://www.shadertoy.com/view/lssGzn
*/

/**
  * Distance field function for a sphere at [0,0,0] of radius `s`
*/
float sdSphere( vec3 p, float s )
{
	return length(p)-s;
}

/**
  * Returns the distance to the closest object in the scene to the given point.
*/
float map( in vec3 p )
{
	return sdSphere(p, 1.0);	
}


/**
  * Returns the distance on the ray with origin `ro` and delta `rd`
  * to the closest object in the scene.
*/
float intersect( in vec3 ro, in vec3 rd )
{
    const float maxd = 20.0;
	float h          = 1.0;
    float t          = 0.0;
    for( int i=0; i<50; i++ )
    {
        if( h<0.001 || t>maxd ) break;
	    h = map( ro+rd*t );
        t += h;
    }

    if( t>maxd ) t=-1.0;
	
    return t;
}

/**
  * `pointlight` is the basic function to evaluate the effect of
  * a light source at a given point. It takes the point position `pos`,
  * the light position `lgt`, the light color `col` and the light
  * `intensity`, and returns the corresponding color.
  *
  * Note that this does not do any shadow casting, so it does not take
  * into account any object that might be between the light and the
  * point.
*/
vec3 pointlight( in vec3 pos, in vec3 lgt, in vec3 col, float intensity ){
    // We start by calculating the distance between the current point
    // and the light source. As light's intensity decreases (ie. is
    // attenuated according to distance), we need this as the primary
    // value.
    float distance    = length(lgt - pos);
    // What we want to calculate next is how the intensity decreases with
    // the distance. In that case, we use a quadratic attenuation, meaning
    // that we divide the intensity by the distance^2. This shading model
    // is super smiple but already gives good results.
    float attenuation = intensity / (distance * distance);
    // We return the corresponding color.
    vec3 color        = col * attenuation;
    return color;
}

/**
  * `lighting` creates three point lights, one red, one green and one blue
  * and return the composite value. The blue light follows the mouse pointer.
*/
vec3 lighting( in vec3 pos ) {
    // In order to make the lighting interesting, we'll make the lights revolve
    // aroud the Y axis at different speeds. This will allow to show how the
    // emitted light blends together. You can adjust the speed of the revolution
    // by changing `s`. Set it to 0.0 to have the lights still.
    float s  = 1.0;
    float a1 = 0.0 * 3.1416 / 3.0 + s*0.5*iTime;
    float a2 = 1.0 * 3.1416 / 3.0 + s*0.3*iTime;
    float a3 = 1.0 * 3.1416 / 3.0 + s*0.1*iTime;
    float r1 = 1.0;
    float r2 = 1.0;
    float r3 = 1.0;
    return pointlight(pos, vec3(r1 * cos(a1), 1.0, r1 * sin(a1)), vec3( 1.0, 0.0, 0.0), 2.0)
    +      pointlight(pos, vec3(r2 * cos(a2), 1.0, r2 * sin(a2)), vec3( 0.0, 1.0, 0.0), 2.0)
    +      pointlight(pos, vec3(r3 * cos(a3), 1.0, r3 * sin(a3)), vec3( 0.0, 0.0, 1.0), 2.0)
    ;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // `q` is the normalized position of the current shaded pixel
	vec2 q = fragCoord.xy / iResolution.xy;
    // `p` is positioned so that [0,0] is at the center of the screen
    vec2 p = 2.0 * q - 1.0;
    // sets aspect ration to `p 
    p.x *= iResolution.x/iResolution.y;  
    
    // `ro` is the camera origin where the rays are cast
	vec3 ro = vec3(0.0, 0.0, 2.0 );
    
    // `rd` is the ray delta, ie. it's normalized direction
    vec3 rd = normalize( vec3(p,-1.0) );
	
    // col is the color of the pixel, black by default.
	vec3 col = vec3(0.0);

    // `t` will hold the distance to the intersection with the scene
    float t = intersect(ro,rd);
    if( t>0.0 )
    {
        // `pos` is the point found by interesect, ie. the point of 
        // where the ray interseted the scene.
        vec3 pos = ro + t*rd;
        // We call the basic `lighting` function to see the point lights
        // in action.
        col  = lighting(pos);
    }
	// We assign the color we've just computed.
    fragColor = vec4( col, 1.0 );
}

// EOF'''


class StDistanceFieldTutorialPart2(GlslSource):
    name = 'st_distance_field_tutorial_part_2'
    label = 'Distance field tutorial part 2'
    description = 'The second part of the distance field raymarching tutorial, where we focus on point lighting with a simplistic shading model.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
