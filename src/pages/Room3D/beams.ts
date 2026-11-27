// Light seen in the air: what a beam's own light, scattered by the haze, sends towards the eye (light.ts §3). Drawn
// into the picture's own real units (cd/m2) and ADDED to what is behind it before the picture is exposed (fog.ts), so
// a beam's weight depends on how bright the room behind it is.
//
// A cone (a par, a mover, a derby's beam, a strobe, a downlight): along each line of sight, the light scattered to the
// eye is  haze x phase x the integral of (candela / distance-from-the-lens squared)  over the stretch of the line that
// lies inside the cone and the room and in front of the first surface. That integral has a closed form (an arctangent),
// so there is no ray marching: one cheap, exact answer per pixel, from any angle, the camera inside the beam or not.
//
// A laser beam is far thinner than a pixel, so it is a line whose brightness carries the whole beam's scattered light:
// haze x phase x lumens / (the width it is drawn at x the sine of the angle it is seen at).

import * as THREE from "three";
import { BEAM_LAYER, beamShared, BOX_GLSL, PHASE_GLSL } from "./fog";
import { PHOTO } from "./light";

const coneVS = `varying vec3 vW; varying vec3 vApex; varying vec3 vAxis;
  void main() { vec4 w = modelMatrix * vec4(position, 1.0); vW = w.xyz;
    vApex = (modelMatrix * vec4(0.0, 0.0, 0.0, 1.0)).xyz;
    vAxis = normalize((modelMatrix * vec4(0.0, 0.0, 1.0, 0.0)).xyz);
    gl_Position = projectionMatrix * viewMatrix * w; }`;
const coneFS = `uniform vec3 cd; uniform float cosA; uniform float cosP;
  uniform float sigma; uniform float scatter; uniform vec3 boxMin; uniform vec3 boxMax;
  uniform sampler2D tDepth; uniform vec2 invRes; uniform mat4 projInv; uniform mat4 viewInv;
  varying vec3 vW; varying vec3 vApex; varying vec3 vAxis;
  ${BOX_GLSL}
  ${PHASE_GLSL}
  // One stretch ta..tb of the line of sight: the light it gathers (F) if it lies in the cone, weighted by the cone's
  // edge falloff where it passes (three samples), and where along it that light mostly comes from (W).
  void part(float ta, float tb, vec3 rd, vec3 co, float tc, float h, inout float F, inout float W) {
    if (tb - ta < 1e-4) return;
    float w = 0.0, pw = 0.0;
    for (int i = 0; i < 3; i++) {
      vec3 q = co + rd * mix(ta, tb, (float(i) + 0.5) / 3.0);
      float r2 = max(dot(q, q), 1e-4), c = dot(q, vAxis) / sqrt(r2);
      w += 1.0 / r2; pw += smoothstep(cosA, cosP, c) / r2;
    }
    if (pw <= 0.0) return;
    float f = (atan((tb - tc) / h) - atan((ta - tc) / h)) / h * (pw / w);
    F += f; W += f * clamp(tc, ta, tb);
  }
  void main() {
    vec2 uv = gl_FragCoord.xy * invRes;
    vec3 rd = normalize(vW - cameraPosition);
    float z = texture2D(tDepth, uv).x;
    vec4 vp = projInv * vec4(uv * 2.0 - 1.0, z * 2.0 - 1.0, 1.0); vp /= vp.w;
    float tScene = length((viewInv * vp).xyz - cameraPosition);        // the first surface along this line
    vec2 span = boxSpan(cameraPosition, rd, boxMin, boxMax);           // the room along it
    float t0 = max(span.x, 0.0), t1 = min(span.y, tScene);
    if (t1 <= t0) discard;
    vec3 co = cameraPosition - vApex;
    float c2 = cosA * cosA, dv = dot(rd, vAxis), cv = dot(co, vAxis);
    float qa = dv * dv - c2, qb = 2.0 * (dv * cv - c2 * dot(rd, co)), qc = cv * cv - c2 * dot(co, co);
    if (abs(qa) < 1e-6) qa = 1e-6;
    float disc = qb * qb - 4.0 * qa * qc, ra = t0, rb = t0;
    if (disc > 0.0) {                                                  // where the line crosses the cone's surface
      float sq = sqrt(disc), r1 = (-qb - sq) / (2.0 * qa), r2 = (-qb + sq) / (2.0 * qa);
      ra = clamp(min(r1, r2), t0, t1); rb = clamp(max(r1, r2), t0, t1);
    }
    float tc = -dot(co, rd);
    float h = sqrt(max(dot(co, co) - tc * tc, 0.0009));                // the line's nearest approach to the lens (3 cm at least)
    float F = 0.0, W = 0.0;
    part(t0, ra, rd, co, tc, h, F, W); part(ra, rb, rd, co, tc, h, F, W); part(rb, t1, rd, co, tc, h, F, W);
    if (F <= 0.0) discard;
    float tm = W / F;
    vec3 q = co + rd * tm; float r = length(q);
    float ph = hazePhase(dot(q / max(r, 1e-4), -rd));
    float att = exp(-sigma * (r + tm - t0));                           // lens -> haze -> eye, through the haze
    gl_FragColor = vec4(cd * (scatter * ph * F * att), 1.0);
  }`;

export type Cone = THREE.Mesh<THREE.ConeGeometry, THREE.ShaderMaterial>;

/** A light's cone in the air: its lens at the mesh's origin, shining along its +z. ``angle``: the cone's half angle
 *  (radians) and ``penumbra`` its soft edge, as the three.js spotlight that lights the surfaces has them. The mesh
 *  only marks where on screen to work the beam out; scale it to reach across the room. */
export function airCone(angle: number, penumbra: number): Cone {
  const g = new THREE.ConeGeometry(Math.tan(Math.min(angle, 1.45)) * 1.08, 1, 28, 1, false);
  g.rotateX(Math.PI);
  g.translate(0, 0.5, 0);
  g.rotateX(Math.PI / 2);                                       // apex at the origin, along +z
  const cosA = Math.cos(angle);
  const m = new THREE.ShaderMaterial({
    uniforms: { ...beamShared, cd: { value: new THREE.Vector3() }, cosA: { value: cosA },
      cosP: { value: Math.max(cosA + 1e-5, Math.cos(angle * (1 - penumbra))) } },
    vertexShader: coneVS, fragmentShader: coneFS, transparent: true, depthWrite: false, depthTest: false,
    blending: THREE.AdditiveBlending, side: THREE.BackSide,
  });
  const mesh = new THREE.Mesh(g, m);
  mesh.frustumCulled = false;
  mesh.visible = false;
  mesh.layers.set(BEAM_LAYER);
  mesh.userData.airCone = true;
  return mesh;
}
/** A cone's light: candela on its axis, as linear red, green, blue. Unseen with no haze in the air. */
export function setCone(mesh: Cone, cd: THREE.Vector3) {
  mesh.material.uniforms.cd.value.copy(cd);
  mesh.visible = beamShared.on.value && Math.max(cd.x, cd.y, cd.z) > 0.05;
}

const lineVS = `attribute vec3 flux; uniform vec3 origin; varying vec3 vC; varying vec3 vW; varying vec3 vO;
  void main() { vC = flux; vec4 w = modelMatrix * vec4(position, 1.0); vW = w.xyz;
    vO = (modelMatrix * vec4(origin, 1.0)).xyz; gl_Position = projectionMatrix * viewMatrix * w; }`;
const lineFS = `uniform float sigma; uniform float scatter; uniform float pxAngle; uniform float aperture; uniform float spread;
  uniform vec3 boxMin; uniform vec3 boxMax; uniform sampler2D tDepth; uniform vec2 invRes;
  varying vec3 vC; varying vec3 vW; varying vec3 vO;
  ${BOX_GLSL}
  ${PHASE_GLSL}
  void main() {
    if (gl_FragCoord.z > texture2D(tDepth, gl_FragCoord.xy * invRes).x + 1e-6) discard;   // behind something
    vec3 e = vW - cameraPosition; float d = length(e); vec3 rd = e / max(d, 1e-4);
    vec3 b = vW - vO; float r = length(b); vec3 bd = b / max(r, 1e-4);
    float sinPsi = max(0.1, length(cross(rd, bd)));                    // seen end-on, more of it lies along the line of sight
    float w = max(d * pxAngle, 0.5 * (aperture + spread * r));         // the width one pixel shows there
    float L = scatter * hazePhase(dot(bd, -rd)) / (sinPsi * w);
    float att = exp(-sigma * (r + boxPath(cameraPosition, vW, boxMin, boxMax)));
    gl_FragColor = vec4(vC * (L * att), 1.0);
  }`;

/** Thin beams in the air (a laser's): line segments from ``origin`` (the aperture, in the geometry's own frame), each
 *  vertex carrying its beam's light in lumens as linear red, green, blue (the ``flux`` attribute). */
export function airLines(segments: number, origin: THREE.Vector3): THREE.LineSegments<THREE.BufferGeometry, THREE.ShaderMaterial> {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(segments * 6), 3));
  g.setAttribute("flux", new THREE.BufferAttribute(new Float32Array(segments * 6), 3));
  const m = new THREE.ShaderMaterial({
    uniforms: { ...beamShared, origin: { value: origin.clone() }, aperture: { value: PHOTO.laser.aperture_m },
      spread: { value: PHOTO.laser.spread_rad } },
    vertexShader: lineVS, fragmentShader: lineFS, transparent: true, depthWrite: false, depthTest: false,
    blending: THREE.AdditiveBlending,
  });
  const lines = new THREE.LineSegments(g, m);
  lines.frustumCulled = false;
  lines.layers.set(BEAM_LAYER);
  lines.userData.airLines = true;          // a beam in the air (scene.ts: drawn only for the light edited, sometimes)
  return lines;
}
