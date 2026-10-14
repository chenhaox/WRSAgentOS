// Adapter to the bundled WRS renderer. All ids/buffers here belong only to this overlay.
const PREFIX = "camera-overlay:";
const CLOUD = PREFIX + "cloud", BOX = PREFIX + "box";
// Display layout only: optical (right, down, forward) -> (forward, left, up).
// This is NOT a camera-to-robot calibration or an executable pose.
const DISPLAY_R = [0, 0, 1, -1, 0, 0, 0, -1, 0], DISPLAY_P = [0, .7, .35];
const IDENTITY_R = [1, 0, 0, 0, 1, 0, 0, 0, 1];
const mv = (r, p) => [0, 1, 2].map(i => r[i*3]*p[0]+r[i*3+1]*p[1]+r[i*3+2]*p[2]);
const add = (a, b) => a.map((v, i) => v+b[i]);
const displayPoint = p => add(mv(DISPLAY_R, p), DISPLAY_P);

function transform(rotation, position, size = [1, 1, 1]) {
  const matrix = new Float32Array(16);
  for (let col=0; col<3; col++) {
    const axis = mv(DISPLAY_R, [rotation[col], rotation[3+col], rotation[6+col]]);
    for (let row=0; row<3; row++) matrix[col*4+row] = axis[row]*size[col];
  }
  matrix.set(displayPoint(position), 12); matrix[15] = 1;
  return matrix;
}

function boxGeometry() {
  const vertices=[], normals=[], faces=[];
  for (let axis=0; axis<3; axis++) for (const sign of [-1, 1]) {
    const first=vertices.length/3, a=(axis+1)%3, b=(axis+2)%3;
    for (const [u,v] of [[-1,-1],[1,-1],[1,1],[-1,1]]) {
      const p=[0,0,0], n=[0,0,0];
      p[axis]=sign*.5; p[a]=u*.5; p[b]=v*.5; n[axis]=sign;
      vertices.push(...p); normals.push(...n);
    }
    const indices=sign>0 ? [0,1,2,0,2,3] : [0,2,1,0,3,2];
    faces.push(...indices.map(i=>first+i));
  }
  return {id:BOX, kind:"mesh", vertices:new Float32Array(vertices),
    normals:new Float32Array(normals), faces:new Uint32Array(faces)};
}

export class CameraScene {
  constructor(host = () => window.wrs) {
    this.host=host; this.renderer=null; this.key=null; this.models=new Set();
    this.geometry=new Set(); this.bounds=null;
  }
  clear() {
    if (this.renderer) {
      for (const id of this.models) this.renderer.remove(id);
      // WRS remove(model) deliberately retains shared geometry. Free only our own geometry.
      for (const id of this.geometry) {
        this.renderer.geometries.get(id)?.buffers.forEach(buffer=>buffer.destroy());
        this.renderer.geometries.delete(id);
      }
    }
    this.models.clear(); this.geometry.clear(); this.key=null; this.bounds=null;
  }
  box(id, rotation, position, size, rgba) {
    id=PREFIX+id;
    this.renderer.add({id, geom:BOX, kind:"mesh", rgba}); this.models.add(id);
    this.renderer.setTransform(id, transform(rotation, position, size));
  }
  update(value) {
    const renderer=this.host()?.renderer;
    if (!renderer) return false;
    const key=value.candidate_set_id || value.observation_id;
    if (renderer===this.renderer && this.key===key && renderer.models.has(CLOUD)) return true;
    this.clear(); this.renderer=renderer;
    try {
      renderer.addGeometry({id:CLOUD, kind:"pcd", points:new Float32Array(value.points.flat()),
        colors:new Float32Array(value.colors.flat().map(c=>c/255))});
      this.geometry.add(CLOUD);
      renderer.add({id:CLOUD, geom:CLOUD, kind:"pcd"}); this.models.add(CLOUD);
      renderer.setTransform(CLOUD, transform(IDENTITY_R, [0,0,0]));
      renderer.addGeometry(boxGeometry()); this.geometry.add(BOX);
      // Camera axes remain in the same displayed coordinate frame as points and grasps.
      for (let axis=0; axis<3; axis++) {
        const p=[0,0,0], size=[.002,.002,.002], color=[.15,.15,.15,1];
        p[axis]=.04; size[axis]=.08; color[axis]=1;
        this.box(`axis:${axis}`, IDENTITY_R, p, size, color);
      }
      for (const [index, grasp] of (value.grasps || []).entries()) {
        // A schematic parallel-jaw candidate: x approach, y closing, z height.
        const w=grasp.width_m, h=grasp.height_m, d=grasp.depth_m, t=.004, back=-.02;
        const bars=[
          {p:[(d+back)/2, -(w+t)/2, 0], size:[d-back,t,h]},
          {p:[(d+back)/2, (w+t)/2, 0], size:[d-back,t,h]},
          {p:[back-t/2,0,0], size:[t,w+2*t,h]},
          {p:[back-.018,0,0], size:[.032,t,t]},
        ];
        const color=index===0 ? [.1,.95,.35,1] : [1,.6,.1,.7];
        bars.forEach((bar,i)=>this.box(`grasp:${index}:${i}`, grasp.rotation,
          add(grasp.translation,mv(grasp.rotation,bar.p)),bar.size,color));
      }
      const lo=[Infinity,Infinity,Infinity], hi=[-Infinity,-Infinity,-Infinity];
      for (const point of value.points) displayPoint(point).forEach((v,i)=>{
        lo[i]=Math.min(lo[i],v); hi[i]=Math.max(hi[i],v);
      });
      if (value.points.length) this.bounds={center:lo.map((v,i)=>(v+hi[i])/2),
        radius:Math.max(.15,Math.hypot(...hi.map((v,i)=>v-lo[i]))/2)};
      this.key=key;
      return true;
    } catch (error) { this.clear(); throw error; }
  }
  focus() {
    const camera=this.host()?.camera;
    if (!camera || !this.bounds) return;
    const {center,radius}=this.bounds;
    camera.up=[0,0,1];
    camera.setTo(add(center,[-2.8*radius,2.2*radius,1.7*radius]),center);
  }
}
