// script.js (main site script)
// Keeps Three.js hero, preloader, lucide, nav, FAQ, timeline, and safe Start Analysis redirect behavior.

// ---------- Safety checks for globals ----------
const hasLucide = typeof window.lucide !== 'undefined';
const hasThree = typeof window.THREE !== 'undefined';

// Initialize Lucide icons (if available)
if (hasLucide && typeof window.lucide.createIcons === 'function') {
  try { window.lucide.createIcons(); } catch (e) { /* ignore */ }
}

// ---------- Mobile Menu Toggle ----------
document.addEventListener('DOMContentLoaded', () => {
  const mobileMenuBtn = document.getElementById("mobile-menu-btn");
  const mobileMenu = document.getElementById("mobile-menu");
  if (mobileMenuBtn && mobileMenu) {
    mobileMenuBtn.addEventListener("click", () => {
      mobileMenu.classList.toggle("hidden");
      const iconName = mobileMenu.classList.contains("hidden") ? "menu" : "x";
      mobileMenuBtn.innerHTML = `<i data-lucide="${iconName}"></i>`;
      if (hasLucide && typeof window.lucide.createIcons === 'function') {
        try { window.lucide.createIcons(); } catch (e) {}
      }
    });
  }

  // FAQ Accordion
  const faqTriggers = document.querySelectorAll(".faq-trigger");
  if (faqTriggers && faqTriggers.length) {
    faqTriggers.forEach((trigger) => {
      trigger.addEventListener("click", () => {
        const content = trigger.nextElementSibling;
        const icon = trigger.querySelector("i");
        if (!content) return;

        const isHidden = content.classList.contains("hidden");
        if (isHidden) {
          faqTriggers.forEach((otherTrigger) => {
            if (otherTrigger !== trigger) {
              const otherContent = otherTrigger.nextElementSibling;
              const otherIcon = otherTrigger.querySelector("i");
              if (otherContent) otherContent.classList.add("hidden");
              if (otherIcon) otherIcon.style.transform = "rotate(0deg)";
            }
          });
          content.classList.remove("hidden");
          if (icon) icon.style.transform = "rotate(180deg)";
        } else {
          content.classList.add("hidden");
          if (icon) icon.style.transform = "rotate(0deg)";
        }
      });
    });
  }
});

// ---------- Hero Title Animation ----------
const titleWords = ["Foren", "Sight"];
const titleContainer = document.getElementById("hero-title");
const subtitle = document.getElementById("hero-subtitle");

if (titleContainer) {
  titleWords.forEach((word, index) => {
    const div = document.createElement("div");
    div.textContent = word;
    div.className = "fade-in mx-2";
    div.style.animationDelay = `${index * 0.13}s`;
    titleContainer.appendChild(div);
  });
}

if (subtitle) {
  setTimeout(() => {
    subtitle.classList.add("fade-in-subtitle");
    subtitle.classList.remove("opacity-0");
  }, 800);
}

// ---------- THREE.JS Scene ----------
function initThreeJS() {
  if (!hasThree) {
    console.warn("THREE.js not found — skipping WebGL background.");
    return;
  }

  const container = document.getElementById("canvas-container");
  if (!container) return;

  const width = container.clientWidth || window.innerWidth;
  const height = container.clientHeight || window.innerHeight;

  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.setSize(width, height);
  renderer.domElement.style.display = "block";
  if (!container.querySelector("canvas")) container.appendChild(renderer.domElement);

  const vertexShader = `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `;

  const fragmentShader = `
    uniform sampler2D uTexture;
    uniform sampler2D uDepthMap;
    uniform vec2 uPointer;
    uniform float uProgress;
    uniform float uTime;
    varying vec2 vUv;

    float random(vec2 st) {
        return fract(sin(dot(st.xy, vec2(12.9898,78.233))) * 43758.5453123);
    }

    float noise(vec2 st) {
        vec2 i = floor(st);
        vec2 f = fract(st);
        float a = random(i);
        float b = random(i + vec2(1.0, 0.0));
        float c = random(i + vec2(0.0, 1.0));
        float d = random(i + vec2(1.0, 1.0));
        vec2 u = f * f * (3.0 - 2.0 * f);
        return mix(a, b, u.x) + (c - a)* u.y * (1.0 - u.x) + (d - b) * u.x * u.y;
    }

    void main() {
        vec2 uv = vUv;
        float depth = texture2D(uDepthMap, uv).r;
        vec2 displacement = depth * uPointer * 0.01;
        vec2 distortedUv = uv + displacement;
        vec4 baseColor = texture2D(uTexture, distortedUv);
        vec2 tUv = vec2(uv.x * 1.0, uv.y);
        vec2 tiling = vec2(120.0);
        vec2 tiledUv = mod(tUv * tiling, 2.0) - 1.0;
        float brightness = noise(tUv * tiling * 0.5);
        float dist = length(tiledUv);
        float dot = smoothstep(0.5, 0.49, dist) * brightness;
        float flow = 1.0 - smoothstep(0.0, 0.02, abs(depth - uProgress));
        vec3 mask = vec3(dot * flow * 10.0, 0.0, 0.0);
        vec3 final = baseColor.rgb + mask;
        gl_FragColor = vec4(final, 1.0);
    }
  `;

  const loader = new THREE.TextureLoader();
  const texture = loader.load("https://i.postimg.cc/XYwvXN8D/img-4.png");
  const depthMap = loader.load("https://i.postimg.cc/2SHKQh2q/raw-4.webp");

  const material = new THREE.ShaderMaterial({
    uniforms: {
      uTexture: { value: texture },
      uDepthMap: { value: depthMap },
      uPointer: { value: new THREE.Vector2(0, 0) },
      uProgress: { value: 0 },
      uTime: { value: 0 },
    },
    vertexShader,
    fragmentShader,
  });

  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material);
  scene.add(mesh);

  const mouse = new THREE.Vector2(0, 0);
  window.addEventListener("mousemove", (event) => {
    mouse.x = (event.clientX / window.innerWidth) * 2 - 1;
    mouse.y = -(event.clientY / window.innerHeight) * 2 + 1;
  });

  const clock = new THREE.Clock();
  function animate() {
    requestAnimationFrame(animate);
    const elapsedTime = clock.getElapsedTime();
    material.uniforms.uTime.value = elapsedTime;
    material.uniforms.uProgress.value = Math.sin(elapsedTime * 0.5) * 0.5 + 0.5;
    material.uniforms.uPointer.value.lerp(mouse, 0.08);
    renderer.render(scene, camera);
  }
  animate();

  window.addEventListener("resize", () => {
    const newWidth = container.clientWidth || window.innerWidth;
    const newHeight = container.clientHeight || window.innerHeight;
    renderer.setSize(newWidth, newHeight);
  });
}

// Initialize Three.js after DOM content exists
if (document.readyState === "complete" || document.readyState === "interactive") {
  setTimeout(initThreeJS, 50);
} else {
  document.addEventListener("DOMContentLoaded", () => setTimeout(initThreeJS, 50));
}

// ---------- Timeline Scroll Animation ----------
const timelineContainer = document.getElementById("timeline-container");
const timelineProgress = document.getElementById("timeline-progress");
if (timelineContainer && timelineProgress) {
  window.addEventListener("scroll", () => {
    const scrollY = window.scrollY || window.pageYOffset;
    const containerTop = timelineContainer.offsetTop;
    const containerHeight = timelineContainer.offsetHeight;
    const windowHeight = window.innerHeight;
    let progress = (scrollY + windowHeight / 2 - containerTop) / containerHeight;
    progress = Math.max(0, Math.min(1, progress));
    timelineProgress.style.height = `${progress * 100}%`;
  });
}

// ---------- Preloader Logic (default 5s visible) ----------
window.addEventListener('load', () => {
  const preloader = document.getElementById('preloader');
  if (!preloader) return;

  setTimeout(() => {
    preloader.classList.add('opacity-0');
    if (!preloader.style.transition) preloader.style.transition = 'opacity 1s ease';
    setTimeout(() => {
      try { if (preloader.parentNode) preloader.parentNode.removeChild(preloader); } catch (e) {}
    }, 1000);
  }, 5000);
});

// ---------- Ensure lucide icons are replaced on load ----------
window.addEventListener('load', () => {
  if (hasLucide && typeof window.lucide.replace === 'function') {
    try { window.lucide.replace(); } catch (e) {}
  }
});

// ==================================================================
// NAVIGATION LOGIC FOR Start Analysis BUTTON (REDIRECT ONLY)
// ==================================================================
// Behavior: Always redirects to dashboard.html.
// Does NOT open file manager. Does NOT scroll.
document.addEventListener('DOMContentLoaded', () => {
  const startBtn = document.getElementById('start-analysis-btn');
  if (!startBtn) return;

  startBtn.addEventListener('click', (e) => {
    e.preventDefault();
    // Strictly redirect to dashboard.html
    window.location.href = 'dashboard.html';
  });
});