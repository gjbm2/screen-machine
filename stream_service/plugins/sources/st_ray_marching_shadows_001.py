"""Ray Marching Shadows 001: PAARDEN ! Basic Raymarching system. Built with the help of user Daedelus.

Source: https://www.shadertoy.com/view/XsdSDN
Author: andro
License: BSD-3-Clause
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: * License: BSD License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''////////////////////////////////////////////////////////////////
//
//             HELPER FUNCTIONS/MACROS
//
////////////////////////////////////////////////////////////////

#define PI 3.14159265
#define TAU (2*PI)
#define PHI (sqrt(5)*0.5 + 0.5)

// Clamp to [0,1] - this operation is free under certain circumstances.
// For further information see
// http://www.humus.name/Articles/Persson_LowLevelThinking.pdf and
// http://www.humus.name/Articles/Persson_LowlevelShaderOptimization.pdf
#define saturate(x) clamp(x, 0, 1)

// Sign function that doesn't return 0
float sgn(float x) {
	return (x<0.0)?-1.0:1.0;
}

vec2 sgn(vec2 v) {
	return vec2((v.x<0.0)?-1.0:1.0, (v.y<0.0)?-1.0:1.0);
}

float square (float x) {
	return x*x;
}

vec2 square (vec2 x) {
	return x*x;
}

vec3 square (vec3 x) {
	return x*x;
}

float lengthSqr(vec3 x) {
	return dot(x, x);
}


// Maximum/minumum elements of a vector
float vmax(vec2 v) {
	return max(v.x, v.y);
}

float vmax(vec3 v) {
	return max(max(v.x, v.y), v.z);
}

float vmax(vec4 v) {
	return max(max(v.x, v.y), max(v.z, v.w));
}

float vmin(vec2 v) {
	return min(v.x, v.y);
}

float vmin(vec3 v) {
	return min(min(v.x, v.y), v.z);
}

float vmin(vec4 v) {
	return min(min(v.x, v.y), min(v.z, v.w));
}



/*
 * # Raymarched distance-field renderer tutorial
 * # Part 1: Basic Distance Field & Raymarching
 *
 * ```
 * Author:  SÃ©bastien Pierre   http://sebastienpierre.ca   @ssebastien
 * License: BSD License
 * ```
 *
 * This shader is meant to show how to implement a raymarching distance field
 * shader-based renderer. It is based on the work of Inigo Quilezles ("iq"), whose
 * amazing code can be see all around on Shadertoy.com.
 *
 * Before editing/reading this shader, you should learn about distance fields and
 * raymarching, in particular [DIST] and [RAY] mentioned below. This tutorial's code
 * is based on the [TRI] code by `iq`.
 *
 * References:
 *
 * - [DIST] http://www.iquilezles.org/www/articles/distfunctions/distfunctions.htm
 * - [TRI]  https://www.shadertoy.com/view/4sXXRN
 * - [RAY]  http://www.iquilezles.org/www/material/nvscene2008/nvscene2008.htm
*/

/**
  * sdSphere is the distance field-based sphere equation as described
  * by iq in [DIST]
*/
float sdSphere( vec3 p, float s )
{
	// The sphere is positioned at the origin (0,0,0) and has a radius of `s`.
	// the distance between the point `p` and the envelope of the shpere is
	// then the distance between the point and the origin, minus the radius
	// of the sphere.
	return length(p)-s;
}

// Box: correct distance to corners
float fBox(vec3 p, vec3 b) 
{
	vec3 d = abs(p) - b;
	return length(max(d, vec3(0))) + vmax(min(d, vec3(0)));
}
/**
  * The map function is where you can register the distance field functions
  * for all the "objects" in your scene. It is fairly simple to do union, interection
  * difference, and repeats, as explained in [DIST]
*/

// Plane with normal n (n is normalized) at some distance from the origin
float fPlane(vec3 p, vec3 n, float distanceFromOrigin) 
{
	return dot(p, n) + distanceFromOrigin;
}

// Rotate around a coordinate axis (i.e. in a plane perpendicular to that axis) by angle <a>.
// Read like this: R(p.xz, a) rotates "x towards z".
// This is fast if <a> is a compile-time constant and slower (but still practical) if not.
void pR(inout vec2 p, float a) 
{
	p = cos(a)*p + sin(a)*vec2(p.y, -p.x);
}

float fOpIntersectionRound(float a, float b, float r) 
{
	vec2 u = max(vec2(r + a,r + b), vec2(0));
	return min(-r, max (a, b)) + length(u);
}

// The "Round" variant uses a quarter-circle to join the two objects smoothly:
float fOpUnionRound(float a, float b, float r) 
{
	vec2 u = max(vec2(r - a,r - b), vec2(0.0));
	return max(r, min (a, b)) - length(u);
}



const float maxd = 100.0;//defines max distance

float map( in vec3 p )//where the magic happens
{
    
    
    pR(p.zx,-0.45);
     vec3 posmod1 = p+vec3(0.0,0.75,0.0);
     float floor1 = fBox(posmod1,vec3(3.0,0.1,3.0));
     float box2 = fBox(vec3(p.x-1.5,p.y+0.3,p.z),vec3(0.375));
   
    
     pR(p.yz,sin(iTime));
    pR(p.xz,cos(iTime));
    float sphere1 = sdSphere(vec3(p.x+0.5,p.y,p.z), 0.5);	
    float box1 = fBox(vec3(p.x+0.5,p.y,p.z),vec3(0.375));
   
   
   
    
    float result1 = max(-sphere1,box1);
    float result2 = fOpUnionRound(floor1,result1 , 0.0); 
    float result3 = fOpUnionRound(box2,result2 , 0.0); 
    return result3;
    //return plane1;
	
}

//SOFT Shadow function from IQ has to be under map.
float softshadow( in vec3 ro, in vec3 rd, float mint, float maxt, float k )
{
    float res = 1.0;
    float t = mint;
    for(int i = 0 ; i < 50 ; ++i)//defines steps for shadows
    {
        float h = map(ro + rd*t);
        if( h<0.001 )
            return 0.0;
        res = min( res, k*h/t );
        t += h;
        if(t > maxt)
            break;
    }
    return res;
}



//GENERATES NORMALS
vec3 fNormal(vec3 intersection, float lastDistance)//generates normals
{
    vec2 epsilon = vec2(0.01, 0.0);
    return normalize((vec3(map(intersection + epsilon.xyy),
    map(intersection + epsilon.yxy),
    map(intersection + epsilon.yyx)) - lastDistance) / epsilon.x);
}

/**
  * The `intersect` function is the main raymarching algorithm. It takes
  * the ray origin `ro` and the ray step (or delta) `rd`. `rd` will be
  * repeatedly added to `ro` until the `map` function (which determines
  * the distance between the given point and the union of all objects
  * in the scene) returns a value that is close to 0 (meaning the current
  * point is almost on the scene object's envelope.
  *
  * Note that if `rd` is not normalized, the steps will increase or
  * decrease as the intersection algorithm go.
*/

float intersect( in vec3 ro, in vec3 rd, out vec3 normal )//This part of the raymarching setup, don't change for now
{
    
    // `h` is the temporary value that we'll use to store the
    // distance to objects in the scene. We could initialize it at any
    // value > 0.001 (the intersection threshold).
	float h          = 1.0;
    // `t` will hold the final result of the raymarching, returning
    // the distance marched on the ray before reaching an intersection.
    float t          = 0.0;
    
    // The number of iterations is limited to 50. I guess this should
    // be adjusted depending on the scene.
    for( int i=0; i<100; i++ )
    {
        // We break if h is below the threshold (ie, we've nearly
        // intersected a scene object), or that we've exceeded the
        // marching distance.
        if( h<0.0 || t>maxd ) break;
        // We get the distance between the current raymarched point 
		// and the union of all objects in the scene. The value returned
        // is the distance to the closest object in the scene.
	    h = map( ro+rd*t );
 		// We add that to the current walking distance. If at the next
        // iteration map returns a value close to 0, it means we'll have
        // intersected, otherwise we'll need to continue.
        t += h;
    }
	
    normal = fNormal(ro + rd * t, h);//calculates normals for lighting
    return t;
}
/*  /// original code from IQ for softshadow
float softshadow( in vec3 ro, in vec3 rd, float mint, float maxt, float k )
{
    float res = 1.0;
    for( float t=mint; t < maxt; )
    {
        float h = map(ro + rd*t);
        if( h<0.001 )
            return 0.0;
        res = min( res, k*h/t );
        t += h;
    }
    return res;
}
*/



void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // `q` is the normalized position of the current shaded pixel, meaning it
    // is between [0,0] and [1,1]
	vec2 q = fragCoord.xy / iResolution.xy;
    
    
    // If you do `p=q`, you will see that the origin is to the bottom left 
    // of the screen. With this simple expression, we adjust the viewpoint
    // in the space and center the origin in the preview screen.
    //
    // vec2 p = q;              // [1] Origin is at the bottom-left of the screen
    // vec2 p = 2.0 * q;        // [2] We scale by 1/2 by multiplying q
    vec2 p = 2.0 * q - 1.0;     // [3] We scale by 1/2 and then center the origin on screen
    // vec2 p = -0.5 + q;       // [4] Alternatively, we can center without scaling
    
    // NOTE: I'm not very familiar with the shader API, but from the above we can deduce
    // that 1.0 means [1.0, 1.0] for a vect2, 2.0 means [2.0, 2.0], etc.
    
    // `iResolution.x/isResolution.y` is the aspect ratio, by default it is 512/288~=1.7777.
    // If you uncomment the following line the image will appear as squashed.
    p.x *= iResolution.x/iResolution.y;  
    
    // The `ro` value specifies the origin of the camera's center in the virtual space.
    // You can tweak the X and Y values to shift the origin, or the Z value to
    // adjust the distance to the sphere (here it is 2.0 to the sphere's center).
	vec3 ro = vec3(0.25, 0.0, 3.0 );
   
    // The `rd` value specifies the  direction of the current pixel (on the projection
    // plane) in the 3D space The notation vec3(p,-1.0) is equivalent to vec3(p.x,p.y,-1.0) as
    // `p` is a vec2. -1.0 for z means it is pointing forward, and the normalization ensures
    // that `rd` can be used as a step for raymarching.
    vec3 rd = normalize( vec3(p,-2.0) );//normally -1.0 Lens distortion ?
	
    // The `col` vector holds the color that will be rendered on the screen, ie. the main
    // output of the shader. As the alpha channel will be set to 1.0, we only need the
    // three components RGB, hence the use of a vec3.
	vec3 col = vec3(0.0);

    // We call the `interect` function with `ro` as the ray origin and `rd` as the 
    // point from which the raymarching step/delta will be calculated. Intersect is the
    // main raymatching function.
     vec3 normal ;
   
    float t = intersect(ro,rd,normal);
   
    vec3 pos = ro + t*rd;
        vec3 nor = fNormal( pos,t );
    // If t > 0.0, it means the ray cast from `ro` through `rd` has intersected with
    // an object of the scene, in which case we'll assign a non-black color to the
    // pixel.
   
        // Here we do the simplest possible shading, which is based on the distance between
        // the ray and the sphere. if `t == 0`, it means the sphere's envelope is intersecting
        // with the current pixel, otherwise t will be the distance between the current pixel
        // and the sphere's envelope. 
        //
  		// NOTE: I'm not sure exactly why we need to substract t from 2.0 and not 1.0. I would
        // assume that because the projection plane is at -1.0 (as set by `rd.z`) and that the
        // sphere is at the origin with a 1.0 radius that there t would be osciallating between
        // 0 (closest) and 1.0 (farthest). If you try changing rd to the following expression:
        //
        // vec3 rd = normalize( vec3(p,-2.0) );
        //
        // You will only make the sphere closer, but the values for `t` will remain the same
        // (the shading will remain). However, if you change the value of `ro` to the following:
        //
        // vec3 ro = vec3(0.0, 0.0, 2.5 );
        //
        // not only will the sphere shrink on the projection, but `t` will also increase. It is
        // not clear to me yet why that is.
        //float d =  max(0.0, 2.0 - t);
         
         //float d =  max(0.0, 1.0-(t/1.0));
    	float d = min(1.0, t/maxd);
        vec3 fog_color = vec3(0.5, 0.5, 0.5);
    	vec3 obj_color = vec3(1.0, 1.0, 1.0);
        
        vec3 light_direction = normalize(vec3(sin(iTime), 1.0, 0.5));
    	//float diffuse = clamp(dot(normal, light_direction) * 0.5 + 0.5, 0.0, 1.0);//backup
   // normal diffuse lighting would be
		float diffuse = clamp(dot(normal, light_direction) , 0.0, 1.0);//improved lighting ?
     //   float diffuse = clamp(dot(normal, light_direction) * 0.5 + 0.5, 0.0, 1.0);//old way of calculating the diffuse light
       float shadow = softshadow (ro + rd * t, light_direction, 0.01, 5.0 , 64.0 );//last number defines shadow quality
        
    	col = mix(obj_color * diffuse *shadow, fog_color, d);
   
	// We assign the color we've just computed.
    fragColor = vec4( col, 1.0 );
}

'''


class StRayMarchingShadows001(GlslSource):
    name = 'st_ray_marching_shadows_001'
    label = 'Ray Marching Shadows 001'
    description = 'PAARDEN ! Basic Raymarching system. Built with the help of user Daedelus.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.4)
