"""Omas Kueche: just playing around without using common random functions

Source: https://www.shadertoy.com/view/lldGW8
Author: vapoi
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Unsere Position, aber mit Ursprung in der Mitte des Bildes
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// ein paar Farben
const vec3 col1 = vec3(0.1, 0.15, 0.72);
const vec3 col2 = vec3(0.3, 0.15, 0.1);
const vec3 colSepia = vec3(1.15, 1.0, 0.9);


void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // Position im Bild, die HÃ¶he pos.y geht von 0.0 (unten) bis 1.0 (oben)
    //					 die Breite pos.x geht von 0.0 (links) bis 1.0 (rechts)
	vec2 pos = fragCoord.xy / iResolution.xy;
    
    // Unsere Position, aber mit Ursprung in der Mitte des Bildes
    vec2 posMid = vec2(-1.0, -1.0) + 2.0 * pos; 
    // HÃ¶he und Breite in VerhÃ¤ltnis setzen
    posMid.x *= iResolution.x / iResolution.y;
    
    // die Zeit in Sekunden: 1.0 entspricht einer Sekunde
    float fTime = iTime * .30;
    
    // Abstand der Position zum Mittelpunkt
    float fDistanceMid = length(posMid);
    
    // Die Farbe des Punktes fÃ¤ngt bei schwarz an
    vec3 colMix = vec3(0.0, 0.0, 0.0);
    
    
    // Hintergrundmuster "irgendwas mit sinus" 
    // Achtung, Sinus liefert auch negative Werte
    float vBack = 0.0;
    vBack += 1.5 * sin(posMid.y * 7.0 + fTime * .70) 
         	 	 * sin(posMid.x * 10.0);
    
    vBack += 1.5 * sin(posMid.y * 31.0) 
        	  	 * sin(posMid.x * 39.0 + fTime * .5);
    
    // Vordergrund Tests
    float fValue = 0.0;
	float fLoop = 0.0;
    float fExtra = 7.0;
    
    for (int j = 0; j < 3; j++)
    {
        fLoop = float(j) * 0.17;
        
        fValue = fValue * 0.1 + sqrt(fract(pos.x * 0.30 * vBack * 0.13 + fLoop + abs(sin(fTime * 01.1 - fLoop))))
            	 + 1.0 + 01.1 * -abs(sin(fDistanceMid * fExtra + fLoop * fValue * vBack + fTime * 0.03))
                 ;
    }
    
    fValue = pow(max(0.0, fValue), 5.0) * 0.15;
    
    
    // Endfarbe zusammenmischen
    colMix = mix(fValue * col2 * 1.5 + fValue * col1 * smoothstep(0.5, 0.8, fDistanceMid) 
                 , colSepia * abs(vBack) * 0.2 
                 , smoothstep(0.1, 0.8, fDistanceMid) * smoothstep(-0.2, 0.3, abs(posMid.y)));
    
    fragColor = vec4(colMix, 1.0);
}'''


class StOmasKueche(GlslSource):
    name = 'st_omas_kueche'
    label = 'Omas Kueche'
    description = 'just playing around without using common random functions'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
