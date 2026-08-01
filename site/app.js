const navToggle = document.querySelector('.nav-toggle');
const nav = document.querySelector('#main-nav');
navToggle?.addEventListener('click', () => {
  const open = nav.classList.toggle('open');
  navToggle.setAttribute('aria-expanded', String(open));
});

const installCommand = 'git clone https://github.com/johnnypatty/blackforge && cd blackforge && bash install.sh';
document.querySelector('[data-copy]')?.addEventListener('click', async (event) => {
  const button = event.currentTarget;
  try {
    await navigator.clipboard.writeText(installCommand);
    button.textContent = 'Copied';
    setTimeout(() => { button.textContent = 'Copy'; }, 1600);
  } catch {
    button.textContent = 'Select text';
  }
});

const observer = 'IntersectionObserver' in window
  ? new IntersectionObserver((entries) => entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        observer.unobserve(entry.target);
      }
    }), { threshold: 0.08 })
  : null;
document.querySelectorAll('.reveal').forEach((element) => observer ? observer.observe(element) : element.classList.add('visible'));

let presets = [];
const grid = document.querySelector('#preset-grid');
const search = document.querySelector('#preset-search');

function escapeText(value) {
  const node = document.createElement('span');
  node.textContent = value;
  return node.innerHTML;
}

function renderPresets(query = '') {
  if (!grid) return;
  const needle = query.trim().toLowerCase();
  const filtered = presets.filter((preset) => [preset.name, preset.description, ...preset.tags, ...preset.packages].join(' ').toLowerCase().includes(needle));
  if (!filtered.length) {
    grid.innerHTML = '<p class="loading">No reviewed preset matches that filter.</p>';
    return;
  }
  grid.innerHTML = filtered.map((preset) => `
    <article class="preset-card">
      <div class="tags">${preset.tags.map((tag) => `<span class="tag">${escapeText(tag)}</span>`).join('')}</div>
      <h3>${escapeText(preset.name)}</h3>
      <p>${escapeText(preset.description)}</p>
      <small>${preset.packages.length} packages · ${escapeText(preset.authors.join(', '))}</small>
      <code>blackforge community apply ${escapeText(preset.id)}</code>
    </article>`).join('');
}

if (grid) {
  fetch('./presets.json', { credentials: 'same-origin' })
    .then((response) => { if (!response.ok) throw new Error('Preset index unavailable'); return response.json(); })
    .then((value) => { presets = Array.isArray(value.presets) ? value.presets : []; renderPresets(); })
    .catch(() => { grid.innerHTML = '<p class="loading">Preset index is temporarily unavailable. Browse it on GitHub.</p>'; });
}
search?.addEventListener('input', () => renderPresets(search.value));

fetch('./meta.json', { credentials: 'same-origin' })
  .then((response) => response.json())
  .then((value) => { const count = document.querySelector('#catalog-count'); if (count && Number.isInteger(value.catalog_tools)) count.textContent = value.catalog_tools.toLocaleString(); })
  .catch(() => {});
