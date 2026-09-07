/* ============================================================
   SARA — Living Space Background v5: star/particle canvas engine
   (unchanged from the v4 optimization pass) + a new lightweight
   parallax hook that shifts the CSS-driven solar system slightly
   opposite the cursor, for a sense of depth relative to the fixed
   starfield behind it. Same public API (window.SaraBackground) —
   no other files need changes.
   ============================================================ */
(() => {
  'use strict';

  const layer = document.getElementById('saraBgLayer');
  if (!layer) return;

  const starsCanvas = document.getElementById('bgStarsCanvas');
  const particlesCanvas = document.getElementById('bgParticlesCanvas');
  const starsCtx = starsCanvas ? starsCanvas.getContext('2d') : null;
  const particlesCtx = particlesCanvas ? particlesCanvas.getContext('2d') : null;
  const solarSystem = document.getElementById('bgSolarSystem');

  const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  const pointerFineQuery = window.matchMedia('(pointer: fine)');
  let reduceMotion = motionQuery.matches;
  let lowPerf = layer.classList.contains('bg-perf-low');

  const TWO_PI = Math.PI * 2;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  let w = 0, h = 0;
  let stars = [];
  let particles = [];
  let streaks = [];
  let streakCount = 0; // logical length into `streaks` (swap-pop pool)
  let rafId = null;
  let running = false;
  let lastTwinkle = 0;
  let resizeTimer = null;
  let nextShootAt = 0;
  let idleHandle = null;

  // ---- brightness-bucket batching setup ----------------------------
  const STAR_BUCKETS = 16;
  const PARTICLE_BUCKETS = 8;
  const PARTICLE_ALPHA_MIN = 0.18;
  const PARTICLE_ALPHA_MAX = 0.42;

  const STAR_RGBA = new Array(STAR_BUCKETS);
  for (let i = 0; i < STAR_BUCKETS; i++) {
    STAR_RGBA[i] = `rgba(255,255,255,${(i / (STAR_BUCKETS - 1)).toFixed(3)})`;
  }
  const PARTICLE_RGBA = new Array(PARTICLE_BUCKETS);
  for (let i = 0; i < PARTICLE_BUCKETS; i++) {
    const a = PARTICLE_ALPHA_MIN + (i / (PARTICLE_BUCKETS - 1)) * (PARTICLE_ALPHA_MAX - PARTICLE_ALPHA_MIN);
    PARTICLE_RGBA[i] = `rgba(210,220,255,${a.toFixed(3)})`;
  }
  const starBuckets = Array.from({ length: STAR_BUCKETS }, () => []);
  const particleBuckets = Array.from({ length: PARTICLE_BUCKETS }, () => []);

  // ---- adaptive frame budget -----------------------------------------
  let frameBudget = 50;
  let avgFrameMs = 16;
  let lastFrameTs = 0;

  function rand(a, b) { return a + Math.random() * (b - a); }
  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

  function runWhenIdle(fn) {
    if (idleHandle !== null) return;
    if (typeof window.requestIdleCallback === 'function') {
      idleHandle = window.requestIdleCallback(() => { idleHandle = null; fn(); }, { timeout: 300 });
    } else {
      idleHandle = window.setTimeout(() => { idleHandle = null; fn(); }, 0);
    }
  }

  function starCount() {
    if (lowPerf) return 70;
    return Math.max(120, Math.min(320, Math.round((w * h) / 6000)));
  }

  function particleCount() {
    return lowPerf ? 0 : 34;
  }

  function sizeCanvas(canvas) {
    if (!canvas) return;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }

  function buildStars() {
    const count = starCount();
    stars = Array.from({ length: count }, () => {
      const bright = Math.random() < 0.1;
      const r = bright ? rand(1.4, 2.2) : rand(0.4, 1.3);
      const x = Math.random() * w;
      const y = Math.random() * h;
      const star = {
        x, y, r,
        baseAlpha: bright ? rand(0.75, 1) : rand(0.3, 0.65),
        phase: rand(0, Math.PI * 2),
        speed: rand(0.25, 0.9),
        bright,
        glow: null
      };
      if (bright && starsCtx) {
        const glow = starsCtx.createRadialGradient(x, y, 0, x, y, r * 5);
        glow.addColorStop(0, 'rgba(210,225,255,1)');
        glow.addColorStop(1, 'rgba(210,225,255,0)');
        star.glow = glow;
      }
      return star;
    });
  }

  function buildParticles() {
    const count = particleCount();
    particles = Array.from({ length: count }, () => {
      const alpha = rand(PARTICLE_ALPHA_MIN, PARTICLE_ALPHA_MAX);
      const bucket = Math.round(
        ((alpha - PARTICLE_ALPHA_MIN) / (PARTICLE_ALPHA_MAX - PARTICLE_ALPHA_MIN)) * (PARTICLE_BUCKETS - 1)
      );
      return {
        x: Math.random() * w,
        y: Math.random() * h,
        r: rand(0.8, 2.4),
        vy: -(rand(0.05, 0.16)),
        vx: rand(-0.03, 0.03),
        bucket
      };
    });
  }

  function resetStreakPool() {
    streaks = [];
    streakCount = 0;
  }

  function spawnShootingStar() {
    const fromLeft = Math.random() < 0.5;
    const startY = rand(0, h * 0.55);
    const speed = rand(7, 12);
    const len = rand(60, 110);
    const streak = {
      x: fromLeft ? -len : w + len,
      y: startY,
      vx: (fromLeft ? 1 : -1) * speed,
      vy: speed * 0.3,
      len,
      life: 1
    };
    if (streakCount < streaks.length) {
      streaks[streakCount] = streak;
    } else {
      streaks.push(streak);
    }
    streakCount++;
  }

  function updateStreaks() {
    for (let i = streakCount - 1; i >= 0; i--) {
      const s = streaks[i];
      s.x += s.vx;
      s.y += s.vy;
      s.life -= 0.018;
      if (s.life <= 0 || s.x < -200 || s.x > w + 200 || s.y > h + 200) {
        streakCount--;
        if (i !== streakCount) {
          streaks[i] = streaks[streakCount];
          streaks[streakCount] = s;
        }
      }
    }
  }

  function drawStreaks(ctx) {
    for (let i = 0; i < streakCount; i++) {
      const s = streaks[i];
      const angle = Math.atan2(s.vy, s.vx);
      const tailX = s.x - Math.cos(angle) * s.len;
      const tailY = s.y - Math.sin(angle) * s.len;
      const grad = ctx.createLinearGradient(s.x, s.y, tailX, tailY);
      grad.addColorStop(0, `rgba(255,255,255,${s.life})`);
      grad.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.strokeStyle = grad;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.moveTo(s.x, s.y);
      ctx.lineTo(tailX, tailY);
      ctx.stroke();
    }
  }

  function drawStars(staticFrame, ts) {
    if (!starsCtx) return;
    starsCtx.clearRect(0, 0, w, h);
    const t = staticFrame ? 0 : ts / 1000;

    for (let i = 0; i < STAR_BUCKETS; i++) starBuckets[i].length = 0;

    for (const s of stars) {
      const twinkle = staticFrame ? 0 : Math.sin(t * s.speed + s.phase) * (s.bright ? 0.3 : 0.22);
      const a = clamp01(s.baseAlpha + twinkle);

      if (s.bright && s.glow) {
        starsCtx.globalAlpha = a * 0.35;
        starsCtx.fillStyle = s.glow;
        starsCtx.beginPath();
        starsCtx.arc(s.x, s.y, s.r * 5, 0, TWO_PI);
        starsCtx.fill();
      }

      const bucket = a <= 0 ? 0 : Math.min(STAR_BUCKETS - 1, Math.round(a * (STAR_BUCKETS - 1)));
      starBuckets[bucket].push(s);
    }

    starsCtx.globalAlpha = 1;
    for (let b = 0; b < STAR_BUCKETS; b++) {
      const list = starBuckets[b];
      if (!list.length) continue;
      starsCtx.beginPath();
      for (const s of list) {
        starsCtx.moveTo(s.x + s.r, s.y);
        starsCtx.arc(s.x, s.y, s.r, 0, TWO_PI);
      }
      starsCtx.fillStyle = STAR_RGBA[b];
      starsCtx.fill();
    }

    if (!staticFrame) {
      if (!lowPerf && ts > nextShootAt) {
        spawnShootingStar();
        nextShootAt = ts + rand(8000, 16000);
      }
      updateStreaks();
      drawStreaks(starsCtx);
    }
  }

  function drawParticles() {
    if (!particlesCtx) return;
    particlesCtx.clearRect(0, 0, w, h);

    for (let i = 0; i < PARTICLE_BUCKETS; i++) particleBuckets[i].length = 0;

    for (const p of particles) {
      p.x += p.vx;
      p.y += p.vy;
      if (p.y < -4) { p.y = h + 4; p.x = Math.random() * w; }
      if (p.x < -4) p.x = w + 4;
      if (p.x > w + 4) p.x = -4;
      particleBuckets[p.bucket].push(p);
    }

    for (let b = 0; b < PARTICLE_BUCKETS; b++) {
      const list = particleBuckets[b];
      if (!list.length) continue;
      particlesCtx.beginPath();
      for (const p of list) {
        particlesCtx.moveTo(p.x + p.r, p.y);
        particlesCtx.arc(p.x, p.y, p.r, 0, TWO_PI);
      }
      particlesCtx.fillStyle = PARTICLE_RGBA[b];
      particlesCtx.fill();
    }
  }

  function applyCanvasTransforms() {
    if (starsCtx) starsCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (particlesCtx) particlesCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function rebuildScene() {
    buildStars();
    buildParticles();
    resetStreakPool();
    drawStars(true, 0);
    drawParticles();
  }

  function resize(deferHeavyRebuild) {
    w = window.innerWidth;
    h = window.innerHeight;
    sizeCanvas(starsCanvas);
    sizeCanvas(particlesCanvas);
    applyCanvasTransforms();
    if (deferHeavyRebuild) {
      runWhenIdle(rebuildScene);
    } else {
      rebuildScene();
    }
  }

  function loop(ts) {
    if (!running) return;

    if (lastFrameTs) {
      const delta = ts - lastFrameTs;
      avgFrameMs = avgFrameMs * 0.9 + delta * 0.1;
      if (avgFrameMs > 40 && frameBudget < 150) frameBudget = Math.min(150, frameBudget + 10);
      else if (avgFrameMs < 20 && frameBudget > 50) frameBudget = Math.max(50, frameBudget - 10);
    }
    lastFrameTs = ts;

    if (ts - lastTwinkle > frameBudget) {
      drawStars(false, ts);
      lastTwinkle = ts;
    }
    drawParticles();
    rafId = requestAnimationFrame(loop);
  }

  function start() {
    if (running) return;
    if (document.hidden) return;
    running = true;
    if (reduceMotion) {
      drawStars(true, 0);
      drawParticles();
      running = false;
      return;
    }
    lastFrameTs = 0;
    rafId = requestAnimationFrame(loop);
  }

  function stop() {
    running = false;
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
  }

  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => resize(true), 150);
  }, { passive: true });

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stop(); else start();
  }, { passive: true });

  motionQuery.addEventListener('change', (e) => {
    reduceMotion = e.matches;
    stop();
    start();
    if (reduceMotion) setParallax(0, 0);
  });

  function init() {
    resize(false);
    start();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // ---- solar-system parallax (cheap: CSS transform on a DOM node,
  // not the canvas, so there's no resolution/blur tradeoff) ----------
  let parallaxScheduled = false;
  let parallaxX = 0, parallaxY = 0;

  function setParallax(x, y) {
    parallaxX = x;
    parallaxY = y;
    if (!solarSystem) return;
    solarSystem.style.setProperty('--sara-px', x.toFixed(1) + 'px');
    solarSystem.style.setProperty('--sara-py', y.toFixed(1) + 'px');
  }

  function onPointerMove(e) {
    if (reduceMotion || !solarSystem) return;
    const nx = (e.clientX / window.innerWidth - 0.5) * -18;
    const ny = (e.clientY / window.innerHeight - 0.5) * -12;
    if (!parallaxScheduled) {
      parallaxScheduled = true;
      requestAnimationFrame(() => {
        parallaxScheduled = false;
        setParallax(nx, ny);
      });
    }
  }

  function updateParallaxListener() {
    window.removeEventListener('mousemove', onPointerMove);
    if (pointerFineQuery.matches && !reduceMotion) {
      window.addEventListener('mousemove', onPointerMove, { passive: true });
    } else {
      setParallax(0, 0);
    }
  }

  updateParallaxListener();
  pointerFineQuery.addEventListener('change', updateParallaxListener);

  const TINT_CLASSES = ['bg-tint-listening', 'bg-tint-thinking', 'bg-tint-speaking'];
  window.SaraBackground = {
    // state: 'listening' | 'thinking' | 'speaking' | null
    setState(state) {
      TINT_CLASSES.forEach(c => layer.classList.remove(c));
      if (state) layer.classList.add(`bg-tint-${state}`);
    },
    clearState() {
      TINT_CLASSES.forEach(c => layer.classList.remove(c));
    },
    // isLow: true drops galaxy/grain/2nd nebula/particles for low-power machines
    setPerfMode(isLow) {
      lowPerf = !!isLow;
      layer.classList.toggle('bg-perf-low', lowPerf);
      buildStars();
      buildParticles();
      resetStreakPool();
    },
    // period: 'day' | 'night'
    setTimeOfDay(period) {
      layer.classList.remove('bg-time-day', 'bg-time-night');
      if (period === 'day' || period === 'night') layer.classList.add(`bg-time-${period}`);
    }
  };
})();