"""Doyle spirals: A port a [url=http://www.fractalforums.com/fragmentarium/doyle-spirals/]fragmentarium shader drawing Doyle spirals[/url]. The original draws it in 3D instead....

Source: https://www.shadertoy.com/view/4tffDH
Author: knighty
License: public-domain
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //Public domain
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//Doyle Spirals. Port from fragmentarium shader done a long time ago.
//Public domain

//Links:
//http://www.fractalforums.com/fragmentarium/doyle-spirals/
//http://www.josleys.com/show_gallery.php?galid=265
//http://www.josleys.com/article_show.php?id=3
//http://klein.math.okstate.edu/IndrasPearls/cusp.pdf

#define PI 3.14159

//Constant parameters: feel free to change these.
//These control the shape of the spiral
const int P = 18;
const int Q = 7;//should be at least 3

//Want do do an inversion?
const bool DoInversion = true;//false;//
//Inversion center
const vec2 InvCenter = vec2(.7,0.);
//Inversion radius squared
const float InvRadius = 1.;

//to change the radius of the discs
float SRadScl = 1.;

//Draw circles?
const bool DrawCircles = true;//false;//
const float DRadius=0.7, Width=1.4, Gamma=2.2;
const vec3 BackgroundColor = vec3(.8);
const vec3 CurveColor = vec3(0.);


//Initializations
//all the initialization calculations done here could (and should) be done in the host program.

//Global variables
mat2 Mat,iMat;
vec4 rads, xps, yps;

//given an etimated z find the solution to Doyle spiral equations using Newton-Raphson method
//The unknowns are the similarities a = exp(z), b = exp(zt) and the radius r.
//The equations are:
//r = ( exp( 2 * z.x ) - 2 * exp( z.x ) * cos( z.y ) + 1 ) / ( exp( z.x ) + 1 )
//r = ( exp( 2 * zt.x) - 2 * exp( zt.x) * cos( zt.y) + 1 ) / ( exp( zt.x) + 1 )
//r = ( exp( 2 * z.x ) - 2 * exp( z.x ) * exp( zt.x) * cos( z.y - zt.y ) + exp( 2 * zt.x ) ) / ( exp( z.x ) + exp( zt.x ) )
//z.x * p = zt.x * q
//z.y * p + 2 * PI = zt.y * q
//In fact the last equation should be:
//   z.y * p + 2 * k * PI = zt.y * q
//Where k is an integer. I haven't esplored other values of k than 1
//For more information see the articles cited above.

vec2 solve( vec2 z ) {
	//Newton-Raphson method
	float k = float( P ) / float( Q );
	for ( int i=0; i<2; i++ ){//2 iterations are usually sufficient: the convergence is very fast. especially when P o=and/or Q are relatively big
		float lb = z.x * k, tb = z.y * k + 2. * PI / float( Q );
		float ra = exp( z.x ),rb = exp( lb );
        float ca = cos( z.y ), cb = cos( tb ), cab = cos( z.y - tb );
		
        //compute function values
		vec3 v = vec3( ( ra * ra - 2. * ra * ca + 1. ) / (( ra + 1. ) * ( ra + 1. )),
					   ( rb * rb - 2. * rb * cb + 1. ) / (( rb + 1. ) * ( rb + 1. )),
					   ( ra * ra - 2. * ra * rb * cab + rb * rb ) / (( ra + rb ) * ( ra + rb )));
		vec2 f = v.xy - v.yz;
		
        //compute jacobian
		vec3 c = 2.* vec3( ra / (( ra + 1. ) * ( ra + 1. )),
                           k * rb / (( rb + 1. ) * ( rb + 1. )),
                           ( 1. - k ) * ra * rb / (( ra + rb ) * ( ra + rb )));
		vec3 v0 = c * vec3( ( 1. + ca ) * ( ra - 1. ) / ( ra + 1. ),
                            ( 1. + cb ) * ( rb - 1. ) / ( rb + 1. ),
                            ( 1. + cab) * ( ra - rb ) / ( ra + rb ));
		vec3 v1 = c * sin( vec3( z.y, tb, z.y - tb ));
		mat2 J  = mat2(0.);
		J[0]    = v0.xy - v0.yz; J[1] = v1.xy - v1.yz;
        
		//compute inverse of J
#if 1
		float idet = 1. / ( J[0][0] * J[1][1] - J[0][1] * J[1][0] );
		mat2 iJ    = -J;
		iJ[0][0]   = J[1][1];
		iJ[1][1]   = J[0][0];
        //next value
		z -= idet * ( iJ * f );
#else
        //of course
        mat2 iJ = inverse(J);
        //but slightly slower
        //next value
		z -= iJ * f;
#endif
	}
	return z;
}

void init() {
	//Find estimate then use Newton Raphson method to refine the solution.
	//Notice that for big P and/or Q the packing will look just like hexagonal one
	//If we take the centers of all packed circles in log-polar plane we will get almost a triangular array
	//That's why I'm using log-polar plane
	//Notice also the link to Drost effect ;)
	//Someone already noticed that before: http://gimpchat.com/viewtopic.php?f=10&t=3941
	
    //Estimate z
    vec2  v  = vec2( -float( P ) + float( Q ) * 0.5 , float( Q ) * sqrt( 3. ) * 0.5 );
	float vd = 1. / length(v);
	float scl = 2. * PI * vd;
	vec2  z  = scl * vd * v.yx;
	
    //Refine. 
    z=solve(z);
	
    //Compute the parameters used for drawing the circle packing
    float k = float( P ) / float( Q );
	
    vec2  zt= vec2( z.x * k, z.y * k + 2. * PI / float( Q ));
	Mat[0]  = z; Mat[1] = zt;
	iMat    = -Mat;
	iMat[0][0] = Mat[1][1]; iMat[1][1] = Mat[0][0];
	iMat *= 1. / ( Mat[0][0] * Mat[1][1] - Mat[0][1] * Mat[1][0] );
	
    float ra = exp( z.x ), rb = exp( zt.x ), ca = cos( z.y );
	float rs = sqrt(( ra * ra - 2. * ra * ca + 1. ) / (( ra + 1. ) * ( ra + 1. )));//radius of the circle centered at (1,0)
	rs *= SRadScl;//for some variations
	rads = rs * vec4( 1., ra, rb, ra * rb );//radius for the 4 circles in the fundamental domain
	xps = vec4( 1., ra * ca, rb * cos( zt.y ), ra * rb * cos( z.y + zt.y ));//Their x coordinates
	yps = vec4( 0., ra * sin( z.y ), rb * sin( zt.y ), ra * rb * sin( z.y + zt.y ));//y
}

//End initializations


vec4 CDoyle( vec2 z ){
	vec2 p = z;
	
    //transform to the plane log-polar
	p = vec2( log( length( p ) ), atan( p.y, p.x ));
	
    //transform into the "oblique" base (defined by z and zt in vinit() function above)
	vec2 pl = iMat * p;
	
    //go to the losange defined by z and zt (as defined in vinit())
	vec2 ip = floor( pl );
	pl = pl - ip;
	
    //back to log-polar plane
	pl = Mat * pl;
	
    //scale and delta-angle
	float scl = exp( pl.x - p.x ), angle = pl.y - p.y;
	
    //the original z is scaled and rotated using scl and angle
	z *= scl;
	float c = cos(angle),s = sin(angle);
	z.xy = z.xy * mat2(vec2( c, -s ), vec2( s, c ));//rotate z
	
    //distances to the discs that are inside the fundamental domain
	vec4 vx = vec4( z.x ) - xps;
	vec4 vy = vec4( z.y ) - yps;
	vec4 dists = sqrt( vx * vx + vy * vy ) - rads;
	
    //take the minimal distance
	float mindist = min( min( dists.x, dists.y ) , min( dists.z, dists.w ) );
		
    //what is the nearest sphere
	bvec4 bvhit = equal( dists, vec4( mindist ) );
	int mindex  = int( dot( vec4( bvhit ), vec4( 0., 1., 2., 3. ) ) );
    
#if 0
    const mat4 set = mat4(vec4(0.),vec4(1.,0.,1.,0.),vec4(0.,1.,1.,0.),vec4(1.,1.,2.,0.));
	vec3  minprop  = set[ mindex ].xyz;
#else
    vec3 minprop;
    if( mindex == 0 )      minprop = vec3(0.);
    else if( mindex == 1 ) minprop = vec3(1.,0.,1.);
    else if( mindex == 2 ) minprop = vec3(0.,1.,1.);
    else                   minprop = vec3(1.,1.,2.);
#endif
	
    vec3 bc = vec3( ip, ip.x + ip.y ) + minprop;
	bc = bc / vec3( P, Q, max( abs( float( P - Q ) ), 1.) );
	bc-= floor( bc );
	
	return vec4( bc, mindist/scl );//For coloring
}

float coverageFunction(float t){
	//this function returns the area of the part of the unit disc that is at the rigth of the verical line x=t.
	//the exact coverage function is:
	//t=clamp(t,-1.,1.); return (acos(t)-t*sqrt(1.-t*t))/PI;
	//this is a good approximation
	return 1. - smoothstep( -1., 1., t );
	//a better approximation:
	//t=clamp(t,-1.,1.); return (t*t*t*t-5.)*t*1./8.+0.5;//but there is no visual difference
}

float coverageLine(float d, float lineWidth, float pixsize){
	d = d * 1. / pixsize;
	float v1 = ( d - 0.5 * lineWidth ) / DRadius;
	float v2 = ( d + 0.5 * lineWidth ) / DRadius;
	return coverageFunction( v1 ) - coverageFunction( v2 );
}

float coverage(float d, float lineWidth, float pixsize){
	d = d * 1. / pixsize;
	float v1 = ( d - 0.5 * lineWidth ) / DRadius;
	return coverageFunction( v1 );
}

vec3 color(vec2 p) {
    float pixsize = dFdx( p.x );
    
    vec4 col = vec4(0.);
    
    float ang = iTime * 0.5;
    float c = cos( ang ), s = sin( ang );
	mat2 rot= mat2( vec2( c, -s ), vec2( s, c ) );
    
    if( DoInversion ) {
		p = p - InvCenter;
		float r2 = dot( p, p );
        float r  = sqrt(r2);
		p = ( InvRadius / r2 ) * p + InvCenter;
        col = CDoyle( rot * p );
        col.w = r2 * col.w / (InvRadius + r * col.w);
	} else
        col = CDoyle( rot * p );
        
    float v=coverage(col.w, Width, pixsize);
    col.rgb = sin( 2. * PI * col.rgb + .0) * 0.5 + 0.5;
    col.rgb = mix( pow(BackgroundColor, vec3(Gamma)), pow(col.rgb, vec3(Gamma)), v);
    if(DrawCircles){
    	v=coverageLine(col.w, Width, pixsize);
    	col.rgb = mix( col.rgb, pow(CurveColor, vec3(Gamma)),v);
    }
    return pow( col.rgb, vec3( 1. / Gamma ) ) ;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	const float scaleFactor = 1.4;
	vec2 uv = scaleFactor * ( fragCoord.xy - 0.5 * iResolution.xy ) / iResolution.y;
    SRadScl = sin( iTime ) * 0.05 + 0.95;
	init(); 
	fragColor = vec4( color( uv ), 1.0 );
}'''


class StDoyleSpirals(GlslSource):
    name = 'st_doyle_spirals'
    label = 'Doyle spirals'
    description = 'A port a [url=http://www.fractalforums.com/fragmentarium/doyle-spirals/]fragmentarium shader drawing Doyle spirals[/url]. The original draws it in 3D instead....'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
