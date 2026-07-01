// script.js - Orb Renderer + App Controller for FRED

class OrbRenderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.dpr = window.devicePixelRatio || 1;
    this.width = 0;
    this.height = 0;
    this.centerX = 0;
    this.centerY = 0;
    this.baseRadius = 0;

    this.state = "idle";
    this.audioLevel = 0;
    this.time = 0;
    this.frameCount = 0;
    this.lastFrameTime = 0;

    this.hue = 160; // idle hue
    this.targetHue = 160;

    this.ripples = [];
    this.particles = [];

    this.resize();
    window.addEventListener("resize", () => this.resize());
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    this.width = rect.width * this.dpr;
    this.height = rect.height * this.dpr;
    this.canvas.width = this.width;
    this.canvas.height = this.height;
    this.canvas.style.width = rect.width + "px";
    this.canvas.style.height = rect.height + "px";
    this.ctx.scale(this.dpr, this.dpr);

    this.centerX = rect.width / 2;
    this.centerY = rect.height / 2;
    this.baseRadius = Math.min(rect.width, rect.height) * 0.32;
  }

  setState(state) {
    this.state = state;
    switch (state) {
      case "idle": this.targetHue = 160; break;
      case "listening": this.targetHue = 190; break;
      case "thinking": this.targetHue = 140; break;
      case "speaking": this.targetHue = 170; break;
    }
  }

  setAudioLevel(level) {
    this.audioLevel = level;
  }

  render(timestamp) {
    if (!this.lastFrameTime) this.lastFrameTime = timestamp;
    const dt = (timestamp - this.lastFrameTime) / 1000;
    this.lastFrameTime = timestamp;
    this.time += dt;
    this.frameCount++;

    // Smooth hue transition
    const hueDiff = (this.targetHue - this.hue + 180) % 360 - 180;
    this.hue += hueDiff * 0.08;

    // Clear
    this.ctx.clearRect(0, 0, this.canvas.width / this.dpr, this.canvas.height / this.dpr);

    // Calculate pulse
    let pulse = 0;
    let pulseAmp = 4;
    let pulseFreq = 0.5;

    switch (this.state) {
      case "idle":
        pulseAmp = 3;
        pulseFreq = 0.45;
        break;
      case "listening":
        pulseAmp = 8 + this.audioLevel * 14;
        pulseFreq = 1.3;
        break;
      case "thinking":
        pulseAmp = 3;
        pulseFreq = 2.2;
        break;
      case "speaking":
        pulseAmp = 6 + this.audioLevel * 18;
        pulseFreq = 1.8;
        break;
    }

    pulse = Math.sin(this.time * pulseFreq * Math.PI * 2) * pulseAmp;
    const radius = this.baseRadius + pulse;

    // Draw aura (outer glow)
    this.drawAura(radius);

    // Draw core sphere with gradient
    this.drawCore(radius);

    // Draw specular highlight
    this.drawHighlight(radius);

    // State-specific decorations
    if (this.state === "thinking") {
      this.drawOrbitParticles(radius, dt);
    } else if (this.state === "listening") {
      this.drawRipples(radius, dt);
    } else if (this.state === "speaking") {
      this.drawFrequencyBars(radius);
    }

    requestAnimationFrame((ts) => this.render(ts));
  }

  drawAura(radius) {
    const gradient = this.ctx.createRadialGradient(
      this.centerX, this.centerY, radius * 0.8,
      this.centerX, this.centerY, radius * 2.2
    );

    const hue = this.hue;
    gradient.addColorStop(0, `hsla(${hue}, 80%, 50%, 0.15)`);
    gradient.addColorStop(0.3, `hsla(${hue}, 70%, 45%, 0.08)`);
    gradient.addColorStop(0.6, `hsla(${hue}, 60%, 40%, 0.03)`);
    gradient.addColorStop(1, `hsla(${hue}, 50%, 35%, 0)`);

    this.ctx.beginPath();
    this.ctx.arc(this.centerX, this.centerY, radius * 2.2, 0, Math.PI * 2);
    this.ctx.fillStyle = gradient;
    this.ctx.fill();
  }

  drawCore(radius) {
    const gradient = this.ctx.createRadialGradient(
      this.centerX - radius * 0.25, this.centerY - radius * 0.3, radius * 0.1,
      this.centerX, this.centerY, radius
    );

    const hue = this.hue;
    gradient.addColorStop(0, `hsla(${hue}, 90%, 65%, 1)`);
    gradient.addColorStop(0.3, `hsla(${hue}, 80%, 50%, 0.95)`);
    gradient.addColorStop(0.6, `hsla(${hue + 15}, 70%, 40%, 0.85)`);
    gradient.addColorStop(1, `hsla(${hue + 30}, 60%, 30%, 0.6)`);

    this.ctx.beginPath();
    this.ctx.arc(this.centerX, this.centerY, radius, 0, Math.PI * 2);
    this.ctx.fillStyle = gradient;
    this.ctx.fill();

    // Inner glass edge
    this.ctx.beginPath();
    this.ctx.arc(this.centerX, this.centerY, radius * 0.92, 0, Math.PI * 2);
    this.ctx.strokeStyle = `hsla(${hue}, 60%, 70%, 0.15)`;
    this.ctx.lineWidth = 1.5;
    this.ctx.stroke();
  }

  drawHighlight(radius) {
    const highlightRadius = radius * 0.35;
    const gradient = this.ctx.createRadialGradient(
      this.centerX - radius * 0.3, this.centerY - radius * 0.35, 0,
      this.centerX - radius * 0.3, this.centerY - radius * 0.35, highlightRadius
    );
    gradient.addColorStop(0, "rgba(255, 255, 255, 0.45)");
    gradient.addColorStop(0.5, "rgba(255, 255, 255, 0.15)");
    gradient.addColorStop(1, "rgba(255, 255, 255, 0)");

    this.ctx.beginPath();
    this.ctx.arc(
      this.centerX - radius * 0.3,
      this.centerY - radius * 0.35,
      highlightRadius,
      0, Math.PI * 2
    );
    this.ctx.fillStyle = gradient;
    this.ctx.fill();
  }

  drawOrbitParticles(radius, dt) {
    const orbitRadius = radius + 22;
    const particleCount = 3;

    // Update existing particles
    if (this.particles.length === 0) {
      for (let i = 0; i < particleCount; i++) {
        this.particles.push({
          angle: (i / particleCount) * Math.PI * 2,
          speed: 0.8 + Math.random() * 0.4,
        });
      }
    }

    this.ctx.save();
    this.ctx.translate(this.centerX, this.centerY);

    for (let i = 0; i < this.particles.length; i++) {
      const p = this.particles[i];
      p.angle += p.speed * dt;
      const x = Math.cos(p.angle) * orbitRadius;
      const y = Math.sin(p.angle) * orbitRadius;

      const particleHue = (this.hue + i * 20) % 360;
      const gradient = this.ctx.createRadialGradient(x, y, 0, x, y, 5);
      gradient.addColorStop(0, `hsla(${particleHue}, 90%, 70%, 1)`);
      gradient.addColorStop(1, `hsla(${particleHue}, 80%, 50%, 0)`);

      this.ctx.beginPath();
      this.ctx.arc(x, y, 5, 0, Math.PI * 2);
      this.ctx.fillStyle = gradient;
      this.ctx.fill();
    }

    this.ctx.restore();
  }

  drawRipples(radius, dt) {
    // Spawn new ripple
    if (this.ripples.length === 0 || this.time - this.ripples[this.ripples.length - 1].time > 0.85) {
      this.ripples.push({ time: this.time, radius: radius });
    }

    // Update and draw
    this.ctx.save();
    this.ctx.translate(this.centerX, this.centerY);

    this.ripples = this.ripples.filter(r => {
      const age = this.time - r.time;
      if (age > 1.4) return false;

      const frac = age / 1.4;
      const currentR = r.radius + age * 80;
      const alpha = (1 - frac) * 0.5;

      this.ctx.beginPath();
      this.ctx.arc(0, 0, currentR, 0, Math.PI * 2);
      this.ctx.strokeStyle = `hsla(${this.hue}, 80%, 55%, ${alpha})`;
      this.ctx.lineWidth = Math.max(1, 3 * (1 - frac));
      this.ctx.stroke();

      return true;
    });

    this.ctx.restore();
  }

  drawFrequencyBars(radius) {
    const barCount = 7;
    const barWidth = 6;
    const gap = 5;
    const totalWidth = barCount * barWidth + (barCount - 1) * gap;
    const startX = this.centerX - totalWidth / 2;
    const baseY = this.centerY + radius + 18;

    this.ctx.save();

    for (let i = 0; i < barCount; i++) {
      const freq = 1.1 + i * 0.35;
      const phase = i * 0.9;
      const height = 8 + 30 * Math.abs(Math.sin(this.time * freq * Math.PI * 2 + phase));
      const x = startX + i * (barWidth + gap);

      const barHue = (this.hue + i * 10) % 360;
      const gradient = this.ctx.createLinearGradient(0, baseY, 0, baseY - height);
      gradient.addColorStop(0, `hsla(${barHue}, 80%, 50%, 0.9)`);
      gradient.addColorStop(1, `hsla(${barHue}, 90%, 70%, 0.3)`);

      this.ctx.beginPath();
      this.ctx.roundRect(x, baseY - height, barWidth, height, 3);
      this.ctx.fillStyle = gradient;
      this.ctx.fill();

      // Glow
      this.ctx.shadowColor = `hsla(${barHue}, 80%, 50%, 0.6)`;
      this.ctx.shadowBlur = 8;
      this.ctx.fill();
      this.ctx.shadowBlur = 0;
    }

    this.ctx.restore();
  }
}

class AppController {
  constructor() {
    this.mode = "compact";
    this.history = [];
    this.historyIndex = -1;
    this.pollInterval = null;

    this.canvas = document.getElementById("orbCanvas");
    this.renderer = new OrbRenderer(this.canvas);
    this.input = document.getElementById("input");
    this.voiceBtn = document.getElementById("voiceBtn");
    this.modeToggle = document.getElementById("modeToggle");
    this.modeIcon = document.getElementById("modeIcon");
    this.transcriptArea = document.getElementById("transcriptArea");
    this.transcriptUser = document.getElementById("transcriptUser");
    this.transcriptFred = document.getElementById("transcriptFred");
    this.stateIndicator = document.getElementById("stateIndicator");
    this.app = document.getElementById("app");
    this.panel = document.getElementById("panel");

    this.bindEvents();
    this.startPolling();
    this.renderer.render(performance.now());
  }

  bindEvents() {
    // Input submit
    this.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        this.submit();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        this.navigateHistory(-1);
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        this.navigateHistory(1);
      } else if (e.key === "Escape") {
        this.handleEscape();
      }
    });

    // Voice button
    this.voiceBtn.addEventListener("click", () => this.toggleVoice());

    // Mode toggle
    this.modeToggle.addEventListener("click", () => this.toggleMode());

    // Panel hover for mode toggle visibility handled by CSS

    // Focus input on click
    this.panel.addEventListener("click", (e) => {
      if (e.target === this.panel) {
        this.input.focus();
      }
    });
  }

  submit() {
    const text = this.input.value.trim();
    if (!text) return;

    this.input.value = "";
    this.historyIndex = this.history.length;
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.on_submit(text);
    }
  }

  navigateHistory(direction) {
    if (this.history.length === 0) return;

    this.historyIndex = Math.max(0, Math.min(this.history.length - 1, this.historyIndex + direction));
    const entry = this.history[this.historyIndex];
    if (entry) {
      this.input.value = entry.user;
    }
  }

  handleEscape() {
    if (this.mode === "fullscreen") {
      this.toggleMode();
    } else {
      if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.minimize_to_tray();
      }
    }
  }

  toggleVoice() {
    this.voiceBtn.classList.toggle("active");
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.on_voice_toggle();
    }
  }

  toggleMode() {
    this.mode = this.mode === "compact" ? "fullscreen" : "compact";
    this.app.className = `app-container mode-${this.mode}`;
    this.updateModeIcon();
    this.input.focus();

    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.on_mode_toggle();
    }
  }

  updateModeIcon() {
    if (this.mode === "compact") {
      this.modeIcon.innerHTML = '<path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>';
    } else {
      this.modeIcon.innerHTML = '<path d="M4 8V4a2 2 0 0 1 2-2h4m12 0h4a2 2 0 0 1 2 2v4m0 12v4a2 2 0 0 1-2 2h-4m-12 0H4a2 2 0 0 1-2-2v-4"/>';
    }
  }

  startPolling() {
    this.pollInterval = setInterval(() => {
      if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.poll_responses();
      }
    }, 100);
  }

  // Called from Python via JS bridge
  setState(state) {
    this.renderer.setState(state);
    this.stateIndicator.className = `state-indicator ${state}`;
    this.stateIndicator.textContent = state;
  }

  setTranscript(userText, fredReply) {
    this.transcriptUser.textContent = userText ? `You: ${userText}` : "";
    this.transcriptFred.textContent = fredReply ? `FRED: ${fredReply}` : "";
    this.transcriptArea.classList.toggle("visible", !!(userText || fredReply));

    if (userText) {
      this.history.push({ user: userText, fred: fredReply || "" });
      this.historyIndex = this.history.length;
    } else if (fredReply && this.history.length > 0) {
      this.history[this.history.length - 1].fred = fredReply;
    }
  }

  setAudioLevel(level) {
    this.renderer.setAudioLevel(level);
  }

  poll_responses() {
    // This is called from Python side, but we expose it for the polling interval
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.poll_responses();
    }
  }
}

// Global functions called from Python
window.setState = (state) => appController?.setState(state);
window.setTranscript = (userText, fredReply) => appController?.setTranscript(userText, fredReply);
window.setAudioLevel = (level) => appController?.setAudioLevel(level);

let appController = null;

document.addEventListener("DOMContentLoaded", () => {
  appController = new AppController();
});