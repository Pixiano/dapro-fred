// === F.R.E.D. Phase 4.5: Core as Aura Option + Rings + Sparks + Aura Arcs + Trails ===

// --- CONFIG ---
let coreAsAura = true; // true = core looks like aura, false = solid core

let randomLines = [];
let ringCount = 50;
let orbitSpeed = 0.005;
let orbitAngle = 0;

let particles = [];
let particleCount = 100;
let auraMinRadius = 140 * 1.3; // 182
let auraMaxRadius = 170 * 1.3; // 221

let sparks = [];
let sparkCount = 50;

let auraArcs = [];
let arcCount = 20; // number of arcs along aura

// --- Trail settings ---
let trailAlpha = 40; // 0 = no trail, 255 = no transparency

function setup() {
  createCanvas(windowWidth, windowHeight, WEBGL);
  colorMode(HSB);
  noFill();

  // Random energy lines
  for (let i = 0; i < 0; i++) randomLines.push(new EnergyLine());

  // Aura particles
  for (let i = 0; i < particleCount; i++) particles.push(new AuraParticle());

  // Sparks along rings
  for (let i = 0; i < sparkCount; i++) sparks.push(new RingSpark());

  // Aura flickering arcs
  for (let i = 0; i < arcCount; i++) auraArcs.push(new AuraArc());
}

function draw() {
  // --- Draw background with trail ---
  drawBackgroundTrail(trailAlpha);

  orbitControl();
  lights();

  // --- Core ---
  let corePulse = 6 * sin(frameCount * 0.05); // pulsing amount
  push();
  noStroke();
  if (coreAsAura) {
    // Core as translucent aura
    noFill();
    for (let r = 0; r <= 120; r += 4) { // slightly smaller than auraMinRadius
      stroke(200, 100, 100, map(sin(frameCount * 0.05 + r), -1, 1, 0.1, 0.3));
      strokeWeight(1);
      sphere(r + corePulse);
    }
  } else {
    // Solid core
    fill('#3bd1ff');
    ambientMaterial('#3bd1ff');
    sphere(156 + corePulse); // 120 * 1.3
  }
  pop();

  // --- Soft glowing aura (gradient) ---
  push();
  noFill();
  for (let r = auraMinRadius; r < auraMaxRadius; r += 5) {
    let alpha = map(sin(frameCount * 0.05 + r), -1, 1, 0.05, 0.3);
    stroke(200, 100, 100, alpha);
    strokeWeight(1.5);
    sphere(r);
  }
  pop();

  // --- Rotating neon rings ---
  for (let i = 0; i < ringCount; i++) {
    push();
    strokeWeight(2);
    colorMode(RGB);
    stroke('#6ef3ff'); // bright neon blue
    colorMode(HSB);
    rotateX((i * PI) / ringCount + sin(frameCount * 0.001 * i));
    rotateY((i * PI) / (ringCount / 2));
    rotateZ(orbitAngle + i * 0.2);
    beginShape();
    for (let a = 0; a < TWO_PI; a += 0.05) {
      let x = auraMinRadius * cos(a);
      let y = auraMinRadius * sin(a);
      vertex(x, y, 0);
    }
    endShape(CLOSE);
    pop();
  }

  // --- Sparks along rings ---
  for (let s of sparks) {
    s.update();
    s.show();
  }

  // --- Random energy lines ---
  for (let line of randomLines) {
    line.update();
    line.show();
  }

  // --- Aura particles ---
  for (let p of particles) {
    p.update();
    p.show();
  }

  // --- Aura flickering arcs ---
  for (let arc of auraArcs) {
    arc.update();
    arc.show();
  }

  orbitAngle += orbitSpeed;
}

// --- Function to draw background with trails ---
function drawBackgroundTrail(alpha) {
  push();
  colorMode(RGB);
  background(0, 0, 0, alpha);
  pop();
}

// === EnergyLine Class ===
class EnergyLine {
  constructor() {
    this.reset();
  }

  reset() {
    this.pos = createVector(random(-200 * 1.3, 200 * 1.3), random(-200 * 1.3, 200 * 1.3), random(-200 * 1.3, 200 * 1.3));
    this.vel = p5.Vector.random3D().mult(1.5);
    this.history = [];
    this.len = int(random(20, 60));
    this.hue = random(180, 210);
  }

  update() {
    this.pos.add(this.vel);
    if (this.pos.mag() > 250 * 1.3) this.reset();
    this.history.push(this.pos.copy());
    if (this.history.length > this.len) this.history.shift();
  }

  show() {
    noFill();
    strokeWeight(2);
    stroke(this.hue, 100, 100, 0.8);
    beginShape();
    for (let v of this.history) vertex(v.x, v.y, v.z);
    endShape();
  }
}

// === AuraParticle Class ===
class AuraParticle {
  constructor() {
    this.radius = random(auraMinRadius, auraMaxRadius);
    this.theta = random(TWO_PI);
    this.phi = random(PI);
    this.speedTheta = random(0.005, 0.02);
    this.speedPhi = random(0.002, 0.01);
    this.hue = random(180, 210);
  }

  update() {
    this.theta += this.speedTheta;
    this.phi += this.speedPhi;
    if (this.phi > PI) this.phi -= PI;
  }

  show() {
    let x = this.radius * sin(this.phi) * cos(this.theta);
    let y = this.radius * cos(this.phi);
    let z = this.radius * sin(this.phi) * sin(this.theta);

    push();
    translate(x, y, z);
    noStroke();
    let c = lerpColor(color(180, 100, 100), color('#3bd1ff'), 0.5);
    fill(c);
    sphere(4);
    pop();
  }
}

// === RingSpark Class ===
class RingSpark {
  constructor() {
    this.radius = auraMinRadius;
    this.angle = random(TWO_PI);
    this.speed = random(0.05, 0.15);
    this.length = random(10, 25);
  }

  update() {
    this.angle += this.speed * 0.02;
  }

  show() {
    let x1 = this.radius * cos(this.angle);
    let y1 = this.radius * sin(this.angle);
    let x2 = this.radius * cos(this.angle + 0.05 * this.length);
    let y2 = this.radius * sin(this.angle + 0.05 * this.length);

    push();
    strokeWeight(2);
    stroke('#6ef3ff'); // bright neon blue
    line(x1, y1, 0, x2, y2, 0);
    pop();
  }
}

// === AuraArc Class (flickering along aura surface) ===
class AuraArc {
  constructor() {
    this.radius = random(auraMinRadius, auraMaxRadius);
    this.theta = random(TWO_PI);
    this.phi = random(PI);
    this.length = random(PI / 12, PI / 6); // arc angular length
    this.alpha = random(150, 255);
    this.life = int(random(10, 30));
    this.age = 0;
  }

  update() {
    this.age++;
    if (this.age > this.life) {
      this.radius = random(auraMinRadius, auraMaxRadius);
      this.theta = random(TWO_PI);
      this.phi = random(PI);
      this.length = random(PI / 12, PI / 6);
      this.alpha = random(150, 255);
      this.life = int(random(10, 30));
      this.age = 0;
    }
  }

  show() {
    let steps = 10;
    beginShape();
    for (let i = 0; i <= steps; i++) {
      let t = i / steps * this.length;
      let x = this.radius * sin(this.phi + t) * cos(this.theta + t);
      let y = this.radius * cos(this.phi + t);
      let z = this.radius * sin(this.phi + t) * sin(this.theta + t);
      vertex(x, y, z);
    }
    stroke('#42f569'); // aura arcs color
    strokeWeight(2);
    noFill();
    endShape();
  }
}